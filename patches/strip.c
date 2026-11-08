/* Rosetta channel strip v2 (docs/STRIP.md). Per analog input 1-8:
 *
 *   in gain -> HPF -> TUBE (+ DC blocker) -> low shelf -> peak -> high shelf -> COMP -> out gain -> limit
 *
 * Runs from wrap48 (IVG10, once per sample frame) BEFORE Roland's 48k chain, so monitor and record both get it.
 * Frames are Q27: full scale FS = 1.0 = 2^27 (Roland's words have 4 bits of headroom above that).
 *
 * The whole state lives in free L1 data (0xFF8034C0.., re/RAM_MAP.md), set up by strip_init() from arm_stub
 * (post-scan). The ISR does nothing unless the magic word is intact. The PC reads the layout from the block
 * itself (header + offset table), so the tool and the build can't disagree about field positions.
 *
 * Biquad coefficients are only ever changed through the mailbox (mb_*): the PC writes the 5 words and the
 * target, then bumps mb_seq; the ISR copies them between two samples and sets mb_done. Single-word params
 * (gains, tube, comp) are poked directly: a word store is atomic for the ISR.
 *
 * No loops with hardware loop registers (built with -fno-branch-count-reg), no RTOS calls, L0-L3 stay 0.
 */
typedef long long s64;

#include "strip.h"

#ifdef HOST_TEST
struct strip strip_host_mem;
#define S (&strip_host_mem)
#else
#define S ((struct strip *)STRIP_ADDR)
#endif

/* ---- fixed-point helpers: floor(a * b / 2^SH), exact, saturating to 32 bits ----
 * a*b = aH bH 2^32 + (aH bL + bH aL) 2^16 + aL bL, with aH/bH signed and aL/bL unsigned halves. */
#ifdef HOST_TEST
static inline int sat32(s64 v)
{
    return v > 0x7fffffffLL ? 0x7fffffff : v < -0x80000000LL ? (int)0x80000000u : (int)v;
}
static inline int mulsh30(int a, int b) { return sat32(((s64)a * b) >> 30); }
static inline int mulsh27(int a, int b) { return sat32(((s64)a * b) >> 27); }
static inline int mulsh24(int a, int b) { return sat32(((s64)a * b) >> 24); }
static inline int sadd(int a, int b) { return sat32((s64)a + b); }
static inline int norm32(int a) { return __builtin_clz((u32)a) - 1; }      /* a > 0 */
#else
#define MULSH(name, R, L)                                                                   \
    static inline int name(int a, int b)                                                    \
    {                                                                                       \
        int r;                                                                              \
        __asm__("A1 = %h1 * %h2 (FU);\n\t"                                                  \
                "A1 = A1 >> 16;\n\t"                                                        \
                "A1 += %d1 * %h2 (M, IS);\n\t"                                              \
                "A1 += %d2 * %h1 (M, IS);\n\t"                                              \
                "A1 = A1 >>> " #R ";\n\t"                                                   \
                "A0 = %d1 * %d2 (IS);\n\t"                                                  \
                "A0 = A0 << " #L ";\n\t"                                                    \
                "%0 = (A0 += A1);"                                                          \
                : "=d"(r) : "d"(a), "d"(b) : "A0", "A1");                                   \
        return r;                                                                           \
    }
MULSH(mulsh30, 14, 2)
MULSH(mulsh27, 11, 5)
MULSH(mulsh24, 8, 8)
static inline int sadd(int a, int b) { return __builtin_bfin_add_fr1x32(a, b); }
static inline int norm32(int a) { return __builtin_bfin_norm_fr1x32(a); }  /* redundant sign bits */
#endif

int strip_mul(int a, int b)             /* on-unit test hooks (octa_lab.py strip selftest) */
{
    return mulsh30(a, b);
}

int strip_mul27(int a, int b)
{
    return mulsh27(a, b);
}

/* ---- biquad: patches/lab1.S _bq_run (hand-written MACs) on the unit, the exact same math in C on the host.
 * P = sum of the 5 products, split by 16-bit halves: P = 2^32 sum(HH) + 2^16 sum(mid) + sum(LL).
 * A1 = (sum(LL) >> 16) + sum(mid) + e;  e' = A1 & 0x3fff (fraction carried to the next sample: error feedback);
 * y = sat32(4 sum(HH) + (A1 >> 14)) = floor((P + carried fraction) / 2^30), then held to +-YMAX. ---- */
#ifdef HOST_TEST
static int bq_run(struct bq *s, int x)
{
    int a[5] = { x, s->x1, s->x2, s->y1, s->y2 };
    s64 ll = 0, mid = 0, hh = 0;
    for (int i = 0; i < 5; i++) {
        s64 aL = a[i] & 0xffff, bL = s->c[i] & 0xffff, aH = a[i] >> 16, bH = s->c[i] >> 16;
        ll += aL * bL;
        mid += aH * bL + bH * aL;
        hh += aH * bH;
    }
    s64 acc = (ll >> 16) + mid + s->e;
    s->e = (u32)acc & 0x3fffu;
    int y = sat32((hh << 2) + (acc >> 14));
    if (y > YMAX - 1)
        y = YMAX - 1;
    else if (y < -YMAX)
        y = -YMAX;
    s->x2 = s->x1;
    s->x1 = x;
    s->y2 = s->y1;
    s->y1 = y;
    return y;
}
#else
extern int bq_run(struct bq *s, int x);
#endif

static inline int biquad(struct bq *s, int x)
{
    return bq_run(s, x);
}

/* ---- tube: asymmetric cubic soft clip c(w) = w - w^3/3 on [-1, 1] (flat +-2/3 outside, smooth at the
 * corner). The bias shifts the operating point: c(w + b) - c(b) has an x^2 term = even harmonics.
 * Polynomial of degree 3 below the knee: harmonics stop at 3x (alias-free for input < Fs/6). ---- */
#define THIRD 44739243                  /* round(2^27 / 3) */

static inline int tube(struct chan *c, int x)
{
    int w = sadd(mulsh27(x, c->drive), c->bias);
    if (w > ONE)
        w = ONE;
    else if (w < -ONE)
        w = -ONE;
    int w3 = mulsh27(mulsh27(w, w), w);
    int y = w - mulsh27(w3, THIRD) - c->c0;
    y = mulsh27(y, c->tout);
    return biquad(&c->bq[4], y);        /* DC blocker (1st-order HPF ~10 Hz, set by the PC) */
}

/* ---- amp: two gain stages of the tube curve (no output gain or DC blocker of their own), then the strip's tone
 * stack, then the speaker biquads. ---- */
static inline int amp_stage(int x, int drive, int bias, int c0)
{
    int w = sadd(mulsh27(x, drive), bias);
    if (w > ONE)
        w = ONE;
    else if (w < -ONE)
        w = -ONE;
    int w3 = mulsh27(mulsh27(w, w), w);
    return w - mulsh27(w3, THIRD) - c0;
}

static inline int amp_gain(struct amp *a, int x)
{
    x = biquad(&a->bq[0], x);
    x = amp_stage(x, a->d1, a->b1, a->c1);
    x = biquad(&a->bq[1], x);
    return amp_stage(x, a->d2, a->b2, a->c2);
}

static inline int amp_speaker(struct amp *a, int x)
{
    x = biquad(&a->bq[2], x);
    x = biquad(&a->bq[3], x);
    x = biquad(&a->bq[4], x);
    x = biquad(&a->bq[5], x);
    return mulsh27(x, a->out);
}

/* ---- comp: feed-forward, log domain, soft knee, attack/release on the gain reduction ---- */
static inline int log2q24(int a)        /* a > 0: log2(a) in Q24 */
{
    int n = norm32(a);
    int t = (a << n) - (1 << 30);       /* mantissa - 1, Q30 */
    int p = mulsh30(t, -91017721);      /* log2(1+t), degree 4, max err 1e-4 (0.0006 dB) */
    p = mulsh30(t, p + 349602085);
    p = mulsh30(t, p - 730082312);
    p = mulsh30(t, p + 1545129915);
    return ((30 - n) << 24) + (p >> 6);
}

static inline int exp2q27(int gl)       /* 2^(gl / 2^24) as a Q27 gain, saturating */
{
    int n = gl >> 24;
    int f = (gl & 0xffffff) << 6;       /* Q30 */
    int p = mulsh30(f, 14582772);       /* 2^f - 1, degree 4, max err 4e-6 */
    p = mulsh30(f, p + 55781443);
    p = mulsh30(f, p + 259250156);
    p = mulsh30(f, p + 744122976);
    int q = (1 << 29) + (p >> 1);       /* 2^f, Q29 */
    int sh = n - 2;
    if (sh >= 2)
        return 0x7fffffff;
    if (sh >= 0)
        return q << sh;
    sh = -sh;
    return sh > 30 ? 0 : q >> sh;
}

static inline int comp(struct chan *c, int x)
{
    int a = x < 0 ? (x == (int)0x80000000u ? 0x7fffffff : -x) : x;
    if (a < 1)
        a = 1;
    int over = log2q24(a) - (27 << 24) - c->thr;
    int gr;
    if (over <= -c->khalf)
        gr = 0;
    else if (over >= c->khalf)
        gr = mulsh30(over, c->slope);
    else {
        int d = over + c->khalf;
        gr = mulsh30(mulsh24(mulsh24(d, d), c->inv2k), c->slope);
    }
    int g = c->g;
    g += mulsh30(gr - g, gr > g ? c->atk : c->rel);
    c->g = g;
    if (g > c->grmax)
        c->grmax = g;
    return mulsh27(x, exp2q27(c->makeup - g));
}

/* ---- per sample ---- */
void strip_pre(int *in)
{
    struct strip *s = S;
    if (s->magic != MAGIC)
        return;
    if (s->mb_seq != s->mb_done) {
        u32 t = s->mb_target, k = t & 15;
        if (k < NBQ + NAMP) {
            struct bq *b = k < NBQ ? &s->ch[(t >> 4) & 7].bq[k] : &s->amp.bq[k - NBQ];
            b->c[0] = s->mb_c[0];
            b->c[1] = s->mb_c[1];
            b->c[2] = s->mb_c[2];
            b->c[3] = s->mb_c[3];
            b->c[4] = s->mb_c[4];
            if (t & 0x100) {
                b->x1 = b->x2 = b->y1 = b->y2 = 0;
                b->e = 0;
            }
        }
        s->mb_done = s->mb_seq;
    }
    struct chan *c = s->ch;
    for (int ch = 0; ch < NCH; ch++, c++) {
        u32 f = c->flags;
        if (!f)
            continue;
        int x = in[ch];
        if (f & F_GAIN)
            x = mulsh27(x, c->gin);
        if (f & F_HPF)
            x = biquad(&c->bq[0], x);
        if (f & F_TUBE)
            x = tube(c, x);
        const int amp_on = (f & F_AMP) && s->amp.ch == (u32)ch;
        if (amp_on)
            x = amp_gain(&s->amp, x);
        if (f & F_LOW)
            x = biquad(&c->bq[1], x);
        if (f & F_PEAK)
            x = biquad(&c->bq[2], x);
        if (f & F_HIGH)
            x = biquad(&c->bq[3], x);
        if (f & F_COMP)
            x = comp(c, x);
        if (amp_on)
            x = amp_speaker(&s->amp, x);
        if (f & F_GAIN)
            x = mulsh27(x, c->gout);
        if (x > OMAX - 1 || x < -OMAX) {
            s->clips++;
            x = x > 0 ? OMAX - 1 : -OMAX;
        }
        in[ch] = x;
    }
}

u32 strip_addr(void)
{
    return (u32)(unsigned long)S;
}

#define OFFC(f) __builtin_offsetof(struct chan, f)

/* Post-scan, from arm_stub (task context, before wrap48 is installed). Everything off, gains unity. */
void strip_init(void)
{
    struct strip *s = S;
    u32 *p = (u32 *)s;
    for (u32 i = 0; i < sizeof(struct strip) / 4; i++)
        p[i] = 0;
    s->ver = VER;
    s->nch = NCH;
    s->chan_size = sizeof(struct chan);
    static const unsigned short offs[] = {
        0, 0,                           /* set below: ch[] offset, sizeof(struct bq) */
        OFFC(flags), OFFC(gin), OFFC(gout), OFFC(bq), OFFC(drive), OFFC(bias), OFFC(c0), OFFC(tout),
        OFFC(thr), OFFC(khalf), OFFC(inv2k), OFFC(slope), OFFC(atk), OFFC(rel), OFFC(makeup), OFFC(g), OFFC(grmax),
    };
    for (u32 i = 0; i < sizeof(offs) / sizeof(offs[0]); i++)
        s->off[i] = offs[i];
    s->off[0] = __builtin_offsetof(struct strip, ch);
    s->off[1] = sizeof(struct bq);
    s->off[19] = __builtin_offsetof(struct strip, amp);
    s->amp.ch = 0xffffffffu;
    s->amp.d1 = s->amp.d2 = s->amp.out = ONE;
    for (int ch = 0; ch < NCH; ch++) {
        s->ch[ch].gin = s->ch[ch].gout = ONE;
        s->ch[ch].drive = s->ch[ch].tout = ONE;
    }
    s->magic = MAGIC;                   /* last: the ISR starts using the block now */
}

/* sizeof(struct strip) must fit the free L1 tail 0xFF8034C0-0xFF803FFF (2880 B) */
typedef char strip_fits_l1[(sizeof(struct strip) <= 0x4000 - 0x34C0) ? 1 : -1];
