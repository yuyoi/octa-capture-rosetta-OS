/* Rosetta STRIP page: takes over Roland's AUTO-SENS setup screen (18) by rewriting its vtable words
 * (re/UI_MAP.md section 7). Reached exactly like AUTO-SENS was: GAIN push on the Channel page.
 *
 *   grid   : channels 1-8 with mini VU + active-block dots. GAIN or VALUE turn / CH </> pick, VALUE push
 *            enters, GAIN push = back to Channel, COMP = straight to that channel's COMP block.
 *   editor : one block per page (GAIN HPF TUBE LOW PEAK HIGH COMP, "n/7"), scrolling parameter list,
 *            always-on input VU + gain-reduction bars. (user's control scheme, 2026-11-07)
 *            GAIN knob = navigate rows, VALUE knob = change the selected value (no edit mode),
 *            CH </> = page, VALUE push = toggle ON rows, GAIN push = back to the grid.
 *   The GAIN page's first row is Roland's preamp gain (same call the Channel page makes per detent).
 *
 * Talks to the DSP only through the strip block (strip.h): coefficient sets via the mailbox, the rest as
 * single words. Runs in the UI task. Built as an upload blob (live test) or linked into the flash build.
 */
#include "strip.h"

typedef short s16;
typedef unsigned short u16;

#define SB ((volatile struct strip *)STRIP_ADDR)
#define VARS ((volatile u32 *)0x41d5e4)             /* +8 PER, +0xc BMAX (lab meter, wrap48) */
#define METER_IN ((volatile u32 *)0x43ed10)         /* input peaks, Q27, refreshed every 50 ms */
#define AS_CH (*(volatile u32 *)0x419000)           /* channel the Channel page hands to screen 18 */
#define CH_SEL (*(volatile u16 *)0x418c50)          /* Roland's Channel page channel */
#define PREAMP 0x43f444
#define CCLK 372400000

/* Roland (re/UI_MAP.md 4.3, 2.2, 3.5) */
#define R_CLEAR(g) ((void (*)(int))0x34e0d4)(g)
#define R_FONT(g, f) ((void (*)(int, int))0x34dfdc)(g, f)
#define R_STR(g, x, y, s) ((int (*)(int, int, int, const char *))0x34edd8)(g, x, y, s)
#define R_LINE(g, x0, y0, x1, y1) ((void (*)(int, int, int, int, int))0x34dd82)(g, x0, y0, x1, y1)
#define R_FILL(g, r) ((void (*)(int, const s16 *))0x34e5fe)(g, r)
#define R_FRAME(g, r) ((void (*)(int, const s16 *))0x34e550)(g, r)
#define R_INVERT(g, r) ((void (*)(int, const s16 *))0x34e756)(g, r)
#define R_BACK() ((void (*)(void))0x3586b8)()
#define R_REDRAW() ((void (*)(void))0x3585ec)()
#define R_SWITCH(n) ((void (*)(int))0x358678)(n)
#define R_STEP(obj, id, dir, ch) ((void (*)(int, int, int, int))0x301670)(obj, id, dir, ch)
#define R_GET(obj, id, ch) ((int (*)(int, int, int))0x3097e4)(obj, id, ch)

enum { K_CH_L, K_CH_R, K_PH48V, K_COMP, K_DISPLAY, K_GAIN_SW, K_CURSOR, K_MIX_SEL };
enum { E_GAIN, E_CURSOR, E_MONITOR };
enum { B_GAIN, B_HPF, B_TUBE, B_LOW, B_PEAK, B_HIGH, B_COMP, NB };
enum { U_ON, U_DB, U_HZ, U_X100, U_RATIO, U_MS10, U_MS, U_PRE };

struct pd {
    const char *name;
    s16 min, max, step, def;            /* step 0 = logarithmic */
    char unit;
};

static const struct pd P_GAIN[] = { { "GAIN", 0, 0, 1, 0, U_PRE }, { "TRIM IN", -240, 240, 5, 0, U_DB },
                                    { "TRIM OUT", -240, 240, 5, 0, U_DB } };
static const struct pd P_HPF[] = { { "ON", 0, 1, 1, 0, U_ON }, { "FREQ", 20, 600, 0, 80, U_HZ },
                                   { "Q", 50, 300, 5, 71, U_X100 } };
static const struct pd P_TUBE[] = { { "ON", 0, 1, 1, 0, U_ON }, { "DRIVE", 0, 240, 5, 60, U_DB },
                                    { "BIAS", 0, 50, 1, 20, U_X100 }, { "OUT", -120, 120, 5, 0, U_DB } };
static const struct pd P_LOW[] = { { "ON", 0, 1, 1, 0, U_ON }, { "FREQ", 30, 1000, 0, 120, U_HZ },
                                   { "GAIN", -150, 150, 5, 0, U_DB } };
static const struct pd P_PEAK[] = { { "ON", 0, 1, 1, 0, U_ON }, { "FREQ", 100, 12000, 0, 1000, U_HZ },
                                    { "GAIN", -150, 150, 5, 0, U_DB }, { "Q", 30, 800, 5, 100, U_X100 } };
static const struct pd P_HIGH[] = { { "ON", 0, 1, 1, 0, U_ON }, { "FREQ", 1000, 16000, 0, 8000, U_HZ },
                                    { "GAIN", -150, 150, 5, 0, U_DB } };
static const struct pd P_COMP[] = { { "ON", 0, 1, 1, 0, U_ON }, { "THRESH", -600, 0, 10, -180, U_DB },
                                    { "RATIO", 10, 200, 1, 40, U_RATIO }, { "ATTACK", 1, 1000, 0, 100, U_MS10 },
                                    { "RELEASE", 10, 2000, 0, 150, U_MS }, { "KNEE", 0, 120, 5, 60, U_DB },
                                    { "MAKEUP", 0, 240, 5, 0, U_DB } };

static const struct { const char *name; const struct pd *p; char n; char flag; } BLK[NB] = {
    { "GAIN", P_GAIN, 3, F_GAIN }, { "HPF", P_HPF, 3, F_HPF }, { "TUBE", P_TUBE, 4, F_TUBE },
    { "LOW", P_LOW, 3, F_LOW }, { "PEAK", P_PEAK, 4, F_PEAK }, { "HIGH", P_HIGH, 3, F_HIGH },
    { "COMP", P_COMP, 7, F_COMP },
};

static s16 val[NCH][NB][8];
static int inited, mode, ch, blk, row, top, editing, ok_rate;
static u16 hold[NCH];
static u16 holdt[NCH];
static int fs;
static int open_blk = -1;              /* Channel page COMP key -> open the STRIP page on that block */

/* ---------------- small math (UI rate only; soft float from libgcc) ---------------- */
static double dsin(double x)            /* |x| <= pi */
{
    const double PI = 3.14159265358979323846;
    if (x > PI / 2)
        x = PI - x;
    else if (x < -PI / 2)
        x = -PI - x;
    double x2 = x * x;
    return x * (1 - x2 / 6 * (1 - x2 / 20 * (1 - x2 / 42 * (1 - x2 / 72 * (1 - x2 / 110 * (1 - x2 / 156))))));
}

static double dcos(double x)            /* 0 <= x <= pi */
{
    const double PI = 3.14159265358979323846;
    return dsin(PI / 2 - x);
}

static double dexp2(double x)
{
    int n = (int)x;
    if (x < n)
        n--;
    double f = x - n, p = 1, t = 1;
    const double LN2 = 0.6931471805599453;
    for (int k = 1; k < 14; k++) {      /* e^(f ln2), f in [0,1) */
        t = t * f * LN2 / k;
        p += t;
    }
    while (n > 0) {
        p *= 2;
        n--;
    }
    while (n < 0) {
        p /= 2;
        n++;
    }
    return p;
}

static double db2lin(double db) { return dexp2(db * 0.16609640474436813); }

static double dsqrt(double a)
{
    if (a <= 0)
        return 0;
    double x = a > 1 ? a : 1;
    for (int i = 0; i < 40; i++)
        x = 0.5 * (x + a / x);
    return x;
}

static int q30(double c)
{
    double v = c * 1073741824.0;
    if (v > 2147483647.0)
        return 0x7fffffff;
    if (v < -2147483648.0)
        return (int)0x80000000u;
    return (int)(v < 0 ? v - 0.5 : v + 0.5);
}

static int q27(double c)
{
    double v = c * 134217728.0;
    if (v > 2147483647.0)
        return 0x7fffffff;
    return (int)(v + 0.5);
}

/* RBJ cookbook, stored as b0 b1 b2 -a1 -a2 (Q30), a0 = 1. kind: 0 hpf, 1 low, 2 peak, 3 high, 4 dc */
static void rbj(int kind, double f, double db, double q, int out[5])
{
    const double PI = 3.14159265358979323846;
    double w = 2 * PI * f / fs, cw = dcos(w), sw = dsin(w), al = sw / (2 * q), A = dexp2(db * 0.08304820237218406);
    double b0, b1, b2, a0, a1, a2;
    if (kind == 0) {
        b0 = (1 + cw) / 2; b1 = -(1 + cw); b2 = b0; a0 = 1 + al; a1 = -2 * cw; a2 = 1 - al;
    } else if (kind == 2) {
        b0 = 1 + al * A; b1 = -2 * cw; b2 = 1 - al * A; a0 = 1 + al / A; a1 = -2 * cw; a2 = 1 - al / A;
    } else if (kind == 1 || kind == 3) {
        double s2 = 2 * dsqrt(A) * al, sg = kind == 1 ? 1 : -1;
        b0 = A * ((A + 1) - sg * (A - 1) * cw + s2);
        b1 = sg * 2 * A * ((A - 1) - sg * (A + 1) * cw);
        b2 = A * ((A + 1) - sg * (A - 1) * cw - s2);
        a0 = (A + 1) + sg * (A - 1) * cw + s2;
        a1 = -sg * 2 * ((A - 1) + sg * (A + 1) * cw);
        a2 = (A + 1) + sg * (A - 1) * cw - s2;
    } else {                            /* 1st-order DC blocker at f */
        double pz = dexp2(-2 * PI * f / fs * 1.4426950408889634);
        b0 = (1 + pz) / 2; b1 = -b0; b2 = 0; a0 = 1; a1 = -pz; a2 = 0;
    }
    out[0] = q30(b0 / a0);
    out[1] = q30(b1 / a0);
    out[2] = q30(b2 / a0);
    out[3] = q30(-a1 / a0);
    out[4] = q30(-a2 / a0);
}

/* ---------------- engine writes ---------------- */
static int mailbox(int c, int slot, const int k[5], int reset)
{
    volatile struct strip *s = SB;
    u32 done = s->mb_done;
    for (int i = 0; i < 5; i++)
        s->mb_c[i] = k[i];
    s->mb_target = (c << 4) | slot | (reset ? 0x100 : 0);
    s->mb_seq = done + 1;
    for (int t = 0; t < 400000; t++)    /* the ISR takes it within one sample (~23 us) */
        if (s->mb_done == done + 1)
            return 1;
    return 0;
}

static void set_flag(int c, int f, int on)
{
    volatile struct chan *k = &SB->ch[c];
    k->flags = on ? (k->flags | f) : (k->flags & ~f);
}

static void apply(int c, int b)
{
    s16 *v = val[c][b];
    volatile struct chan *k = &SB->ch[c];
    if (SB->magic != MAGIC || !ok_rate)
        return;
    int was = k->flags & BLK[b].flag;
    switch (b) {
    case B_GAIN:
        k->gin = q27(db2lin(v[1] / 10.0));
        k->gout = q27(db2lin(v[2] / 10.0));
        set_flag(c, F_GAIN, v[1] || v[2]);
        return;
    case B_HPF: case B_LOW: case B_PEAK: case B_HIGH: {
        if (!v[0]) {
            set_flag(c, BLK[b].flag, 0);
            return;
        }
        int co[5];
        static const char slot[NB] = { 0, 0, 0, 1, 2, 3, 0 };
        double q = b == B_HPF ? v[2] / 100.0 : b == B_PEAK ? v[3] / 100.0 : 0.7071;
        rbj(b == B_HPF ? 0 : b == B_LOW ? 1 : b == B_PEAK ? 2 : 3, v[1], b == B_HPF ? 0 : v[2] / 10.0, q, co);
        if (mailbox(c, slot[b], co, !was))
            set_flag(c, BLK[b].flag, 1);
        return;
    }
    case B_TUBE: {
        if (!v[0]) {
            set_flag(c, F_TUBE, 0);
            return;
        }
        double drive = db2lin(v[1] / 10.0), bias = v[2] / 100.0;
        double tout = db2lin(v[3] / 10.0) / (drive * (1 - bias * bias));
        k->drive = q27(drive);
        k->bias = q27(bias);
        k->c0 = q27(bias - bias * bias * bias / 3);
        k->tout = q27(tout > 15.99 ? 15.99 : tout);
        if (!was) {
            int co[5];
            rbj(4, 10, 0, 1, co);
            if (!mailbox(c, 4, co, 1))
                return;
        }
        set_flag(c, F_TUBE, 1);
        return;
    }
    case B_COMP: {
        if (!v[0]) {
            set_flag(c, F_COMP, 0);
            return;
        }
        const double L2DB = 6.020599913279624, Q24 = 16777216.0;
        double knee = v[5] / 10.0 / L2DB, ratio = v[2] / 10.0;
        k->thr = (int)(v[1] / 10.0 / L2DB * Q24);
        k->khalf = (int)(knee / 2 * Q24);
        k->inv2k = knee > 0 ? (int)(Q24 / (2 * knee)) : 0;
        k->slope = v[2] >= 200 ? 0x40000000 : q30(1 - 1 / ratio);
        k->atk = q30(1 - dexp2(-10000.0 / (v[3] * (double)fs) * 1.4426950408889634));
        k->rel = q30(1 - dexp2(-1000.0 / (v[4] * (double)fs) * 1.4426950408889634));
        k->makeup = (int)(v[6] / 10.0 / L2DB * Q24);
        if (!was) {
            k->g = 0;
            k->grmax = 0;
        }
        set_flag(c, F_COMP, 1);
        return;
    }
    }
}

/* ---------------- text ---------------- */
static char *put_int(char *p, int v, int digits_after, int sign)
{
    char t[12];
    int n = 0, neg = v < 0;
    if (neg)
        v = -v;
    int whole = v, frac = 0, div = 1;
    for (int i = 0; i < digits_after; i++)
        div *= 10;
    if (digits_after) {
        whole = v / div;
        frac = v % div;
    }
    do {
        t[n++] = '0' + whole % 10;
        whole /= 10;
    } while (whole);
    if (neg)
        *p++ = '-';
    else if (sign)
        *p++ = '+';
    while (n)
        *p++ = t[--n];
    if (digits_after) {
        *p++ = '.';
        for (int d = div / 10; d; d /= 10)
            *p++ = '0' + (frac / d) % 10;
    }
    *p = 0;
    return p;
}

static int slen(const char *s)
{
    int n = 0;
    while (s[n])
        n++;
    return n;
}

static char *put_str(char *p, const char *s)
{
    while (*s)
        *p++ = *s++;
    *p = 0;
    return p;
}

static void fmt(char *p, int unit, int v)
{
    switch (unit) {
    case U_ON: put_str(p, v ? "ON" : "OFF"); break;
    case U_DB: put_str(put_int(p, v, 1, 1), "DB"); break;
    case U_HZ:
        if (v >= 1000)
            put_str(put_int(p, v / 10, 2, 0), "K");
        else
            put_str(put_int(p, v, 0, 0), "HZ");
        break;
    case U_X100: put_int(p, v, 2, 0); break;
    case U_RATIO:
        if (v >= 200)
            put_str(p, "INF:1");
        else
            put_str(put_int(p, v, 1, 0), ":1");
        break;
    case U_MS10: put_str(put_int(p, v, 1, 0), "MS"); break;
    case U_MS: put_str(put_int(p, v, 0, 0), "MS"); break;
    case U_PRE: put_int(p, v, 0, 0); break;
    }
}

/* ---------------- meters ---------------- */
static int peak_px(u32 p, int h)        /* Q27 peak -> 0..h pixels over -60..0 dBFS */
{
    if (p < 134218)                     /* -60 dBFS */
        return 0;
    int n = __builtin_clz(p);           /* log2(p) = 31 - n + frac */
    int frac8 = (int)((p << n) >> 28) & 7;
    int l8 = (31 - n - 27) * 8 + frac8; /* log2(p / 2^27) in eighths, -80..+8 */
    int db10 = l8 * 602 / 80;           /* 0.1 dB */
    if (db10 > 0)
        db10 = 0;
    return (600 + db10) * h / 600;
}

static void vbar(int g, int x0, int y0, int x1, int y1, int fill, int down)
{
    s16 r[4] = { (s16)x0, (s16)y0, (s16)x1, (s16)y1 };
    R_FRAME(g, r);
    if (fill <= 0)
        return;
    if (down) {
        r[1] = y0 + 1; r[3] = y0 + fill;
    } else {
        r[1] = y1 - fill; r[3] = y1 - 1;
    }
    r[0] = x0 + 1; r[2] = x1 - 1;
    if (r[3] >= r[1])
        R_FILL(g, r);
}

static void meters_tick(void)
{
    for (int c = 0; c < NCH; c++) {
        int px = peak_px(METER_IN[c], 60);
        if (px >= hold[c]) {
            hold[c] = px;
            holdt[c] = 20;
        } else if (holdt[c]) {
            holdt[c]--;
        } else if (hold[c]) {
            hold[c] -= hold[c] > 3 ? 3 : hold[c];
        }
    }
}

/* ---------------- drawing ---------------- */
static void init_vals(void)
{
    for (int c = 0; c < NCH; c++)
        for (int b = 0; b < NB; b++)
            for (int i = 0; i < BLK[b].n; i++)
                val[c][b][i] = BLK[b].p[i].def;
    inited = 1;
}

static void update_rate(void)
{
    u32 per = VARS[2];
    fs = per ? CCLK / (int)per : 0;
    int r = fs < 46000 ? 44100 : fs < 68000 ? 48000 : 88200;
    ok_rate = per && fs > 40000 && fs < 92000;
    fs = r;
}

static void draw_grid(int g)
{
    char t[24];
    R_FONT(g, 18);
    R_STR(g, 0, 0, "STRIP");
    u32 per = VARS[2], busy = VARS[3];
    char *p = put_str(t, ok_rate ? "CPU " : "96K+ OFF ");
    if (ok_rate) {
        p = put_int(p, per ? (int)(busy * 100 / per) : 0, 0, 0);
        put_str(p, "%");
    }
    R_STR(g, 70, 0, t);
    R_LINE(g, 0, 11, 127, 11);
    for (int c = 0; c < NCH; c++) {
        int x = (c & 3) * 32, y = 13 + (c >> 2) * 25;
        s16 r[4] = { (s16)(x + 1), (s16)y, (s16)(x + 30), (s16)(y + 23) };
        R_FRAME(g, r);
        t[0] = '1' + c;
        t[1] = 0;
        R_FONT(g, 13);
        R_STR(g, x + 4, y + 3, t);
        vbar(g, x + 24, y + 2, x + 28, y + 21, peak_px(METER_IN[c], 18), 0);
        u32 f = SB->magic == MAGIC ? SB->ch[c].flags : 0;
        for (int b = 0; b < NB; b++) {
            s16 d[4] = { (s16)(x + 3 + b * 3), (s16)(y + 17), (s16)(x + 4 + b * 3), (s16)(y + 20) };
            if (f & BLK[b].flag)
                R_FILL(g, d);
            else {
                d[1] = d[3];
                R_FILL(g, d);
            }
        }
        if (c == ch) {
            s16 s[4] = { (s16)(x + 2), (s16)(y + 1), (s16)(x + 22), (s16)(y + 14) };
            R_INVERT(g, s);
        }
    }
}

static void draw_editor(int g)
{
    char t[24];
    const struct pd *p = BLK[blk].p;
    int n = BLK[blk].n;
    R_FONT(g, 18);
    t[0] = 'C'; t[1] = 'H'; t[2] = '1' + ch; t[3] = ' ';
    put_str(t + 4, BLK[blk].name);
    R_STR(g, 0, 0, t);
    t[0] = '1' + blk; t[1] = '/'; t[2] = '7'; t[3] = 0;
    R_STR(g, 92, 0, t);
    R_LINE(g, 0, 11, 114, 11);
    if (row < top)
        top = row;
    if (row >= top + 5)
        top = row - 4;
    for (int i = 0; i < 5 && top + i < n; i++) {
        int k = top + i, y = 13 + i * 10;
        R_STR(g, 2, y, p[k].name);
        char v[16];
        fmt(v, p[k].unit, p[k].unit == U_PRE ? R_GET(PREAMP, 0, ch) : val[ch][blk][k]);
        int w = (int)slen(v) * 6;
        R_STR(g, 113 - w, y, v);
        if (k == row) {
            s16 r[4] = { 0, (s16)(y - 1), 114, (s16)(y + 9) };
            R_INVERT(g, r);
        }
    }
    if (top > 0)
        R_LINE(g, 110, 12, 112, 12);
    if (top + 5 < n)
        R_LINE(g, 110, 63, 112, 63);
    vbar(g, 117, 0, 121, 63, hold[ch], 0);
    int gr = 0;
    if (SB->magic == MAGIC && (SB->ch[ch].flags & F_COMP))
        gr = (int)((long long)SB->ch[ch].g * 602 / (100LL << 24)) * 3;    /* 3 px per dB */
    vbar(g, 123, 0, 127, 63, gr > 62 ? 62 : gr, 1);
}

/* ---------------- screen methods (vtable slots of screen 18) ---------------- */
int pg_create(int self)
{
    (void)self;
    if (!inited)
        init_vals();
    mode = 0;
    ch = AS_CH & 7;
    editing = 0;
    update_rate();
    if (open_blk >= 0) {
        mode = 1;
        blk = open_blk;
        row = 0;
        top = 0;
    }
    open_blk = -1;
    return 0;
}

int pg_destroy(int self)
{
    (void)self;
    return 0;
}

int pg_draw(int self)
{
    int g = *(int *)(self + 0x28);
    update_rate();
    meters_tick();
    R_CLEAR(g);
    if (mode == 0)
        draw_grid(g);
    else
        draw_editor(g);
    return 0;
}

static void enter(int b)
{
    mode = 1;
    blk = b;
    row = 0;
    top = 0;
    editing = 0;
}

int pg_key(int self, int key)
{
    (void)self;
    switch (key) {
    case K_GAIN_SW:
        if (mode) {
            mode = 0;
            R_REDRAW();
        } else {
            R_BACK();
        }
        return 1;
    case K_CURSOR:
        if (!mode) {
            enter(0);
        } else if (BLK[blk].p[row].unit == U_ON) {
            val[ch][blk][row] ^= 1;
            apply(ch, blk);
        }
        R_REDRAW();
        return 1;
    case K_CH_L:
    case K_CH_R: {
        int d = key == K_CH_R ? 1 : -1;
        if (!mode)
            ch = (ch + d + NCH) & 7;
        else
            enter((blk + d + NB) % NB);
        R_REDRAW();
        return 1;
    }
    case K_COMP:
        enter(B_COMP);
        R_REDRAW();
        return 1;
    case K_PH48V:
        return 1;
    default:
        return 0;                       /* DISPLAY (hold -> SYSTEM, release -> HOME), MIX_SEL: stock */
    }
}

int pg_keyup(int self, int key)
{
    (void)self;
    return key == K_DISPLAY || key == K_MIX_SEL ? 0 : 1;
}

static void change(int delta)           /* VALUE knob: the selected row's value */
{
    const struct pd *p = &BLK[blk].p[row];
    if (p->unit == U_PRE) {             /* Roland preamp gain, one stock step per detent */
        int d = delta > 0 ? 1 : -1;
        for (int i = delta > 0 ? delta : -delta; i > 0; i--)
            R_STEP(PREAMP, 0, d, ch);
        return;
    }
    s16 *v = &val[ch][blk][row];
    int x = *v;
    if (p->unit == U_ON) {
        x = delta > 0;
    } else {
        int st = p->step ? p->step : (x / 24 > 1 ? x / 24 : 1);
        x += st * delta;
        if (x < p->min)
            x = p->min;
        if (x > p->max)
            x = p->max;
    }
    if (x != *v) {
        *v = x;
        apply(ch, blk);
    }
}

int pg_enc(int self, int id, int delta)
{
    (void)self;
    int d = delta > 0 ? 1 : -1;
    if (id == E_MONITOR)
        return 0;                       /* stock (monitor level) */
    if (!mode) {                        /* grid: either knob picks the channel */
        ch = (ch + d + NCH) & 7;
    } else if (id == E_GAIN) {          /* navigate rows */
        int r = row + d;
        if (r >= 0 && r < BLK[blk].n)
            row = r;
    } else {
        change(delta);
    }
    R_REDRAW();
    return 1;
}

/* ================= Rosetta Channel page (screen 2, vtable 0x401538) =================
 * User spec (2026-11-07): GAIN big in dB + a VU (wave view later), nothing else. 48V tag only when on, CLIP flag.
 * CH </> and VALUE turn = channel, GAIN turn = preamp gain, GAIN push = STRIP page, COMP = STRIP comp block,
 * 48V = toggle phantom, DISPLAY / MIX_SEL = stock. Preamp gain raw / 2 = dB (measured on the unit). */
static u16 clip_t[NCH];

static int cur_ch(void)
{
    return CH_SEL & 7;
}

int chp_create(int self)
{
    (void)self;
    if (!inited)
        init_vals();
    if (CH_SEL > 7)
        CH_SEL = 0;
    return 0;
}

/* Version C (user pick, 2026-11-08): a lit tick ring like a pot on the left, number + segment VU right. */
#define R_RANGE(obj, id, mn, mx, ch) ((void (*)(int, int, int *, int *, int))0x30987a)(obj, id, mn, mx, ch)
static const s16 KN[49][2] = {{-181,181}, {-198,162}, {-213,142}, {-226,121}, {-237,98}, {-245,74}, {-251,50},
    {-255,25}, {-256,0}, {-255,-25}, {-251,-50}, {-245,-74}, {-237,-98}, {-226,-121}, {-213,-142}, {-198,-162},
    {-181,-181}, {-162,-198}, {-142,-213}, {-121,-226}, {-98,-237}, {-74,-245}, {-50,-251}, {-25,-255}, {0,-256},
    {25,-255}, {50,-251}, {74,-245}, {98,-237}, {121,-226}, {142,-213}, {162,-198}, {181,-181}, {198,-162},
    {213,-142}, {226,-121}, {237,-98}, {245,-74}, {251,-50}, {255,-25}, {256,0}, {255,25}, {251,50}, {245,74},
    {237,98}, {226,121}, {213,142}, {198,162}, {181,181}};          /* 225 .. -45 deg, 270 deg sweep, x256 */
static const signed char RING[24][2] = {{12,0}, {12,3}, {10,6}, {8,8}, {6,10}, {3,12}, {0,12}, {-3,12}, {-6,10},
    {-8,8}, {-10,6}, {-12,3}, {-12,0}, {-12,-3}, {-10,-6}, {-8,-8}, {-6,-10}, {-3,-12}, {0,-12}, {3,-12}, {6,-10},
    {8,-8}, {10,-6}, {12,-3}};

#define KX 26
#define KY 30

static int kr(int v, int r)                     /* v (x256) * r, rounded */
{
    int p = v * r;
    return (p + (p >= 0 ? 128 : -128)) / 256;
}

static void kline(int g, int k, int r0, int r1, int dx, int dy)
{
    R_LINE(g, KX + kr(KN[k][0], r0) + dx, KY + kr(KN[k][1], r0) + dy, KX + kr(KN[k][0], r1) + dx,
           KY + kr(KN[k][1], r1) + dy);
}

static void put_db(char *t, int raw)            /* half-dB steps -> "29.5" */
{
    if (raw < 0) {
        *t++ = '-';
        raw = -raw;
    }
    char *q = put_int(t, raw / 2, 0, 0);
    *q++ = '.';
    *q++ = raw & 1 ? '5' : '0';
    *q = 0;
}

int chp_draw(int self)
{
    int g = *(int *)(self + 0x28);
    int c = cur_ch();
    char t[24];
    meters_tick();
    u32 pk = METER_IN[c];
    if (pk >= 0x07f00000u)                      /* within ~0.1 dB of full scale: the preamp/ADC clipped */
        clip_t[c] = 20;
    else if (clip_t[c])
        clip_t[c]--;
    int raw = R_GET(PREAMP, 0, c);              /* half-dB steps */
    int mn = 0, mx = 0;
    R_RANGE(PREAMP, 0, &mn, &mx, c);
    if (mn > mx) {
        int x = mn; mn = mx; mx = x;
    }
    if (mx <= mn) {
        mn = 0;
        mx = 88;
    }
    int k = raw <= mn ? 0 : raw >= mx ? 48 : (raw - mn) * 48 / (mx - mn);
    R_CLEAR(g);

    /* knob: 25 ticks, lit ones long + 2 px wide, inner ring, pointer */
    for (int i = 0; i <= 24; i++) {
        int x = KN[2 * i][0], y = KN[2 * i][1];
        if (2 * i <= k) {                       /* lit: long, doubled across the tick direction */
            int side = (x < 0 ? -x : x) > (y < 0 ? -y : y);
            kline(g, 2 * i, 14, 20, 0, 0);
            kline(g, 2 * i, 14, 20, !side, side);
        } else {
            kline(g, 2 * i, 18, 20, 0, 0);
        }
    }
    for (int i = 0; i < 24; i++) {
        int j = (i + 1) % 24;
        R_LINE(g, KX + RING[i][0], KY + RING[i][1], KX + RING[j][0], KY + RING[j][1]);
    }
    kline(g, k, 3, 10, 0, 0);
    R_FONT(g, 12);                              /* whole dB under the ring ends ("0.0" + "50.0" overlapped) */
    put_int(t, mn / 2, 0, 0);
    R_STR(g, 6, 55, t);
    put_int(t, mx / 2, 0, 0);
    int w = (int)slen(t) * 6;
    R_STR(g, 47 - w, 55, t);

    /* right panel */
    R_FONT(g, 18);
    t[0] = 'C'; t[1] = 'H'; t[2] = ' '; t[3] = '1' + c; t[4] = 0;
    R_STR(g, 56, 0, t);
    if (clip_t[c]) {
        R_STR(g, 82, 0, "CLIP");
        s16 r[4] = { 80, 0, 106, 10 };
        R_INVERT(g, r);
    }
    if (R_GET(PREAMP, 11, c)) {
        R_STR(g, 108, 0, "48V");
        s16 r[4] = { 107, 0, 127, 10 };
        R_INVERT(g, r);
    }
    put_db(t, raw);
    R_FONT(g, 6);
    w = R_STR(g, 56, 12, t);
    R_FONT(g, 18);
    R_STR(g, 56 + (w > 0 && w < 70 ? w : 40) + 3, 23, "DB");
    R_FONT(g, 12);
    R_STR(g, 56, 36, "GAIN");

    /* segment VU: 24 x (2 px + 1 gap) = x 56..127, -60..0 dBFS, peak-hold segment, top 6 dB hollow */
    int lit = peak_px(pk, 24), hs = hold[c] * 24 / 60;
    for (int i = 0; i < 24; i++) {
        s16 r[4] = { (s16)(56 + i * 3), 45, (s16)(57 + i * 3), 52 };
        if (i < lit || i == hs - 1)
            R_FILL(g, r);
        else if (i >= 21) {                     /* hot zone, unlit: top + bottom dots (a frame read as lit) */
            R_LINE(g, r[0], 45, r[2], 45);
            R_LINE(g, r[0], 52, r[2], 52);
        } else
            R_LINE(g, r[0], 52, r[0], 52);
    }
    R_STR(g, 56, 56, "-60");
    R_STR(g, 86, 56, "-30");
    R_STR(g, 122, 56, "0");
    return 0;
}

int chp_key(int self, int key)
{
    (void)self;
    int c = cur_ch();
    switch (key) {
    case K_CH_L:
        CH_SEL = (c + 7) & 7;
        break;
    case K_CH_R:
        CH_SEL = (c + 1) & 7;
        break;
    case K_GAIN_SW:
    case K_COMP:
        AS_CH = c;
        open_blk = key == K_COMP ? B_COMP : -1;
        R_SWITCH(18);
        return 1;
    case K_PH48V:
        R_STEP(PREAMP, 11, R_GET(PREAMP, 11, c) ? -1 : 1, c);
        break;
    case K_CURSOR:
        return 1;
    default:
        return 0;                               /* DISPLAY, MIX_SEL: stock */
    }
    R_REDRAW();
    return 1;
}

int chp_keyup(int self, int key)
{
    (void)self;
    return key == K_DISPLAY || key == K_MIX_SEL ? 0 : 1;
}

int chp_enc(int self, int id, int delta)
{
    (void)self;
    int c = cur_ch(), d = delta > 0 ? 1 : -1;
    if (id == E_GAIN) {
        for (int i = delta > 0 ? delta : -delta; i > 0; i--)
            R_STEP(PREAMP, 0, d, c);
    } else if (id == E_CURSOR) {
        CH_SEL = (c + d + NCH) & 7;
    } else {
        return 0;                               /* MONITOR: stock */
    }
    R_REDRAW();
    return 1;
}

/* Flash build: called once from arm_stub (post-scan, task context, boot = HOME, so screens 2/18 are not live).
 * Points screen 18 (AUTO-SENS setup) at the STRIP page, screen 2 (Channel) at the Rosetta Channel page, and
 * renames the preamp label SENS -> GAIN. */
void page_install(void)
{
    static const u32 slot[12] = { 0x402434, 0x40243c, 0x402444, 0x40244c, 0x40245c, 0x402484,   /* screen 18 */
                                  0x401554, 0x40155c, 0x401564, 0x40156c, 0x40157c, 0x4015a4 }; /* screen 2 */
    const u32 fn[12] = { (u32)pg_create, (u32)pg_destroy, (u32)pg_key, (u32)pg_keyup, (u32)pg_draw, (u32)pg_enc,
                         (u32)chp_create, (u32)pg_destroy, (u32)chp_key, (u32)chp_keyup, (u32)chp_draw, (u32)chp_enc };
    for (int i = 0; i < 12; i++)
        *(volatile u32 *)slot[i] = fn[i];
    volatile char *lab = (volatile char *)0x41c7a0;     /* "SENS" (param 0 name, 8-byte slot) */
    if (lab[0] == 'S' && lab[1] == 'E' && lab[2] == 'N' && lab[3] == 'S' && lab[4] == 0) {
        lab[0] = 'G'; lab[1] = 'A'; lab[2] = 'I'; lab[3] = 'N';
    }
}
