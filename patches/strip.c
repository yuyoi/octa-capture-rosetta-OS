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
struct fx strip_host_fx;
#define FX (&strip_host_fx)
struct gate strip_host_gate;
#define GT (&strip_host_gate)
#else
#define S ((struct strip *)STRIP_ADDR)
#define FX ((struct fx *)FX_ADDR)
#define GT ((struct gate *)GATE_ADDR)
#endif

#if defined(FAST_BLOB) || defined(HOST_TEST)
#define HAVE_SLIM
#ifdef HOST_TEST
struct slimc strip_host_slim[NCH];
#define SL strip_host_slim
#else
#define SL ((struct slimc *)SLIM_ADDR)
#endif
#endif

#define LIKELY(x) __builtin_expect(!!(x), 1)
#define UNLIKELY(x) __builtin_expect(!!(x), 0)

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

#ifndef FAST_BLOB
int strip_mul(int a, int b)             /* on-unit test hooks (octa_lab.py strip selftest) */
{
    return mulsh30(a, b);
}

int strip_mul27(int a, int b)
{
    return mulsh27(a, b);
}
#endif

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

#ifdef FAST_BLOB
extern int bq_run3(struct bq *s, int x);        /* fast.S: same result for a stage with c[2] = c[4] = 0, 8 multiplies fewer */
#else
#define bq_run3 bq_run
#endif

#ifdef HOST_TEST
#define bq_slim bq_run                          /* identical when the low halves of b0-b2 are zero, which is what SLIM sends */
#elif defined(FAST_BLOB)
extern int bq_slim(struct bq *s, int x);        /* fast.S: 14 multiplies instead of 20 */
#endif

#ifdef FAST_BLOB
extern int bq_fast(struct bq *s, int x);        /* fast.S: bq_run's sums with the loads overlapped, same output.
                                                   Inlining it was tried: bit-exact, but ~20 cycles slower in the ISR. */
static inline int biquad(struct bq *s, int x)
{
    return bq_fast(s, x);
}
#else
static inline int biquad(struct bq *s, int x)
{
    return bq_run(s, x);
}
#endif

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
    return bq_run3(&c->bq[4], y);       /* DC blocker (1st-order HPF ~10 Hz, set by the PC) */
}

/* ---- amp: two gain stages of the tube curve (no output gain or DC blocker of their own), then the strip's tone
 * stack, then the speaker biquads. F_AMP_HARD: the stages clip hard instead, at the curve's own ceiling (+-2/3), so the
 * level matches; the bias still shifts the clip point (uneven clipping = even harmonics), minus itself so no DC step.
 * Hard clipping makes harmonics past Fs/2 that fold back (fizz) at mid gain; at full gain the two sound alike. ---- */
#define TWO3 89478485                   /* round(2^27 * 2/3) */

static inline int amp_stage(int x, int drive, int bias, int c0, u32 hard)
{
    int w = sadd(mulsh27(x, drive), bias);
    if (hard) {
        w = w > TWO3 ? TWO3 : w < -TWO3 ? -TWO3 : w;
        return w - bias;
    }
    if (w > ONE)
        w = ONE;
    else if (w < -ONE)
        w = -ONE;
    int w3 = mulsh27(mulsh27(w, w), w);
    return w - mulsh27(w3, THIRD) - c0;
}

static inline int amp_gain(struct amp *a, int x, u32 hard)
{
    x = biquad(&a->bq[0], x);
    x = amp_stage(x, a->d1, a->b1, a->c1, hard);
    x = biquad(&a->bq[1], x);
    return amp_stage(x, a->d2, a->b2, a->c2, hard);
}

static inline int amp_speaker(struct amp *a, int x)
{
    x = biquad(&a->bq[2], x);
    x = biquad(&a->bq[3], x);
    x = biquad(&a->bq[4], x);
    x = biquad(&a->bq[5], x);
    return mulsh27(x, a->out);
}

/* ---- echo + reverb (strip.h struct fx) ---- */
static inline int sat16(int v)
{
    return v > 32767 ? 32767 : v < -32768 ? -32768 : v;
}
#define TO16(x) ((short)sat16((x) >> 12))
#define FROM16(v) ((int)(v) * 4096)

static int fx_run(int x)
{
    struct fx *f = FX;
    if (f->magic != FX_MAGIC)
        return x;
    int wet = 0;
    u32 n = f->dlen;
    if (n) {                            /* echo: read n back, darken, feed back, write */
        if (n > FX_DMAX)
            n = FX_DMAX;
        u32 p = f->dpos;
        if (p >= FX_DMAX)
            p = 0;
        u32 r = p >= n ? p - n : p + FX_DMAX - n;
        int d = FROM16(f->dbuf[r]);
        f->dlp = sadd(f->dlp, mulsh27(f->ddamp, d - f->dlp));
        f->dbuf[p] = TO16(sadd(x, mulsh27(f->dlp, f->dfb)));
        f->dpos = p + 1 >= FX_DMAX ? 0 : p + 1;
        wet = mulsh27(f->dlp, f->dmix);
    }
    if (f->rmix) {                      /* reverb on dry + echo, 1/8 in so the combs never clip */
        int in = sadd(x, wet) >> 3, sum = 0;
        for (int i = 0; i < FX_NC; i++) {
            u32 L = f->clen[i], p = f->cpos[i];
            if (L - 1 >= FX_CMAX)
                L = FX_CMAX;
            if (p >= L)
                p = 0;
            int c = FROM16(f->cbuf[i][p]);
            f->clp[i] = sadd(f->clp[i], mulsh27(f->rdamp, c - f->clp[i]));
            f->cbuf[i][p] = TO16(sadd(in, mulsh27(f->clp[i], f->rfb)));
            f->cpos[i] = p + 1 >= L ? 0 : p + 1;
            sum = sadd(sum, c);
        }
        for (int j = 0; j < FX_NA; j++) {
            u32 L = f->alen[j], p = f->apos[j];
            if (L - 1 >= FX_AMAX)
                L = FX_AMAX;
            if (p >= L)
                p = 0;
            int b = FROM16(f->abuf[j][p]);
            f->abuf[j][p] = TO16(sadd(sum, b >> 1));
            f->apos[j] = p + 1 >= L ? 0 : p + 1;
            sum = b - sum;
        }
        wet = sadd(wet, mulsh27(sum, f->rmix));
    }
    return sadd(x, wet);
}

/* ---- noise gate (strip.h struct gate): detect on x0 (the input as it came in), turn x down ---- */
static int gate_run(int ch, int x0, int x)
{
    struct gate *G = GT;
    if (G->magic != GATE_MAGIC)
        return x;
    struct gate_ch *k = &G->ch[ch];
    int a = x0 < 0 ? (x0 == (int)0x80000000 ? 0x7fffffff : -x0) : x0;
    k->env = a > k->env ? a : k->env - (k->env >> 9);          /* peak follower, ~12 ms fall */
    if (k->env > k->open) {
        k->is_open = 1;
        k->cnt = (u32)G->hold;
    } else if (k->is_open && k->env < k->close) {
        if (k->cnt)
            k->cnt--;
        else
            k->is_open = 0;
    }
    if (k->is_open) {                   /* +1 so it lands exactly on 1.0: an open gate is bit-transparent */
        k->g = sadd(k->g, mulsh30(ONE - k->g, G->att) + 1);
        if (k->g > ONE)
            k->g = ONE;
    } else
        k->g -= mulsh30(k->g, G->rel);
    if (k->g < 0)
        k->g = 0;
    return mulsh27(x, k->g);
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

static inline int abs32(int x)
{
    return x < 0 ? (x == (int)0x80000000u ? 0x7fffffff : -x) : x;
}

static inline int comp_update(struct chan *c, int a)   /* level -> new gain reduction */
{
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
    return g;
}

static inline int comp(struct chan *c, int x)
{
    return mulsh27(x, exp2q27(c->makeup - comp_update(c, abs32(x))));
}

#ifdef HAVE_SLIM
static inline int comp_slim(struct chan *c, struct slimc *l, int x)
{
    int a = abs32(x);
    if (a > l->pk)
        l->pk = a;
    if (--l->n < 0) {
        l->n = COMP_DIV - 1;
        l->lin = exp2q27(c->makeup - comp_update(c, l->pk));
        l->pk = 0;
    }
    return mulsh27(x, l->lin);
}
#endif

/* ---- per sample ---- */
void strip_pre(int *in)
{
    struct strip *s = S;
    if (s->magic != MAGIC)
        return;
    if (UNLIKELY(s->mb_seq != s->mb_done)) {
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
    /* Which inputs have anything on, without a branch per input: on this core every taken branch costs 6-10 cycles,
     * and visiting 8 empty inputs one by one cost ~300 cycles a sample (measured). Then only the active ones are visited. */
#define ON(n) ((u32)(s->ch[n].flags != 0) << (n))
    u32 on = ON(0) | ON(1) | ON(2) | ON(3) | ON(4) | ON(5) | ON(6) | ON(7);
#undef ON
    while (on) {
        const int ch = 30 - norm32((int)(on & -on));   /* lowest set bit */
        on &= on - 1;
        struct chan *c = &s->ch[ch];
        u32 f = c->flags;
        int x = in[ch];
        const int x0 = x;                               /* the gate listens to the input as it came in */
#ifdef HAVE_SLIM
#define PEAK_STAGE(x) ((f & F_SLIM_PEAK) ? bq_slim(&c->bq[2], x) : biquad(&c->bq[2], x))
#define HIGH_STAGE(x) ((f & F_SLIM_HIGH) ? bq_slim(&c->bq[3], x) : biquad(&c->bq[3], x))
#define COMP_STAGE(x) ((f & F_COMP_DIV) ? comp_slim(c, &SL[ch], x) : comp(c, x))
#else
#define PEAK_STAGE(x) biquad(&c->bq[2], x)
#define HIGH_STAGE(x) biquad(&c->bq[3], x)
#define COMP_STAGE(x) comp(c, x)
#endif
        if (LIKELY((f & ~(u32)(F_SLIM_PEAK | F_SLIM_HIGH | F_COMP_DIV)) ==
                   (F_HPF | F_TUBE | F_LOW | F_PEAK | F_HIGH | F_COMP))) {
            /* the whole strip and nothing else, the usual case: straight through without testing each block (every
             * test is a branch, 6-10 cycles here) */
            x = biquad(&c->bq[0], x);
            x = tube(c, x);
            x = biquad(&c->bq[1], x);
            x = PEAK_STAGE(x);
            x = HIGH_STAGE(x);
            x = COMP_STAGE(x);
        } else {
            if (UNLIKELY(f & F_GAIN))
                x = mulsh27(x, c->gin);
            if (f & F_HPF)
                x = biquad(&c->bq[0], x);
            if (f & F_TUBE)
                x = tube(c, x);
            const int amp_on = UNLIKELY(f & F_AMP) && s->amp.ch == (u32)ch;
            if (amp_on)
                x = amp_gain(&s->amp, x, f & F_AMP_HARD);
            if (f & F_LOW)
                x = biquad(&c->bq[1], x);
            if (f & F_PEAK)
                x = PEAK_STAGE(x);
            if (f & F_HIGH)
                x = HIGH_STAGE(x);
            if (f & F_COMP)
                x = COMP_STAGE(x);
            if (amp_on)
                x = amp_speaker(&s->amp, x);
            if (UNLIKELY(f & F_GAIN))
                x = mulsh27(x, c->gout);
        }
#undef PEAK_STAGE
#undef HIGH_STAGE
#undef COMP_STAGE
        if (UNLIKELY(f & F_GATE))
            x = gate_run(ch, x0, x);
        if (UNLIKELY(f & F_FX))
            x = fx_run(x);
        if (UNLIKELY(x > OMAX - 1 || x < -OMAX)) {
            s->clips++;
            x = x > 0 ? OMAX - 1 : -OMAX;
        }
        in[ch] = x;
    }
}

#ifndef FAST_BLOB
u32 strip_addr(void)
{
    return (u32)(unsigned long)S;
}

u32 fx_addr(void)
{
    return (u32)(unsigned long)FX;
}

u32 gate_addr(void)
{
    return (u32)(unsigned long)GT;
}

static void gate_init(void)             /* all open-ish defaults; nothing is gated until F_GATE is set */
{
    struct gate *G = GT;
    G->magic = 0;
    G->hold = 2646;                     /* 60 ms at 44.1 kHz */
    G->att = 78000000;                  /* ~0.3 ms (Q30 0.073) */
    G->rel = 410000;                    /* ~60 ms (Q30 0.00038) */
    for (int c = 0; c < NCH; c++) {
        struct gate_ch *k = &G->ch[c];
        k->open = 754000;               /* -45 dBFS */
        k->close = 378000;              /* -51 dBFS */
        k->env = 0;
        k->g = ONE;
        k->cnt = (u32)G->hold;
        k->is_open = 1;
    }
    G->magic = GATE_MAGIC;
}

/* everything silent and off; the classic small-room lengths at 44.1 kHz until the PC sets its own */
static void fx_init(void)
{
    struct fx *f = FX;
    f->magic = 0;
    u32 *p = (u32 *)f;
    for (u32 i = 0; i < sizeof(struct fx) / 4; i++)
        p[i] = 0;
    static const unsigned short cl[FX_NC] = { 1116, 1188, 1277, 1356 }, al[FX_NA] = { 556, 441 };
    for (int i = 0; i < FX_NC; i++)
        f->clen[i] = cl[i];
    for (int j = 0; j < FX_NA; j++)
        f->alen[j] = al[j];
    f->ddamp = f->rdamp = ONE;
    f->magic = FX_MAGIC;
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
#ifdef HOST_TEST
    for (int ch = 0; ch < NCH; ch++) {
        SL[ch].pk = 0;
        SL[ch].n = ch;
        SL[ch].lin = ONE;
    }
#endif
    fx_init();
    gate_init();
    s->magic = MAGIC;                   /* last: the ISR starts using the block now */
}

#endif /* !FAST_BLOB */

/* sizeof(struct strip) must fit the free L1 tail 0xFF8034C0-0xFF803FFF (2880 B) */
typedef char strip_fits_l1[(sizeof(struct strip) <= 0x4000 - 0x34C0) ? 1 : -1];
