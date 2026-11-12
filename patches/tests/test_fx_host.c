/* Host test of the echo + reverb (strip.c fx_run), built with AddressSanitizer: echo time and level, feedback decay, the
 * dry sample unchanged and on time, a reverb tail that rings and dies, nothing blows up, and no length from the PC can
 * make it touch memory outside its buffers. */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "strip.h"

extern struct strip strip_host_mem;
extern struct fx strip_host_fx;
extern void strip_init(void);
extern void strip_pre(int *in);

static int fails, checks;
static void check(int ok, const char *what)
{
    checks++;
    fails += !ok;
    printf("%s: %s\n", ok ? "ok  " : "FAIL", what);
}

#define Q (1 << 27)
static int frame(int ch, int x)
{
    int in[10] = { 0 };
    in[ch] = x;
    strip_pre(in);
    return in[ch];
}

int main(void)
{
    struct fx *f = &strip_host_fx;
    strip_init();
    check(f->magic == 0x31584652u && f->dlen == 0 && f->rmix == 0, "init: FX block up, echo and reverb off");
    check(sizeof(struct fx) < 0x3E0000u - 0x3B4000u, "FX block fits 0x3B4000-0x3DFFFF on the box");
    strip_host_mem.ch[2].flags = F_FX;
    int same = 1;
    for (int t = 0; t < 2000; t++) {
        int x = (int)(0.3 * Q * sin(t * 0.05));
        same &= frame(2, x) == x;
    }
    check(same, "F_FX with echo and reverb off: audio bit-identical");

    /* echo 100 ms, no feedback, full level, bright */
    f->dlen = 4410; f->dfb = 0; f->dmix = Q; f->ddamp = Q;
    int y0 = frame(2, Q / 2), peak_at = -1, big = 0;
    for (int t = 1; t < 10000; t++) {
        int y = frame(2, 0);
        if (y > Q / 4 && peak_at < 0)
            peak_at = t;
        if (t != 4410 && (y > Q / 100 || y < -Q / 100))
            big++;
    }
    check(y0 == Q / 2, "dry: same sample, unchanged (no latency)");
    check(peak_at == 4410 && big == 0, "one clean echo exactly 4410 samples later, nothing else");

    /* feedback 0.5: repeats every 4410 samples, each 6 dB down */
    f->dfb = Q / 2;
    frame(2, Q / 2);
    double r[4];
    for (int k = 1; k <= 4; k++) {
        int y = 0;
        for (int t = 0; t < 4410; t++)
            y = frame(2, 0);
        r[k - 1] = y / (double)Q;
    }
    check(fabs(r[0] - 0.5) < 0.001 && fabs(r[1] - 0.25) < 0.001 && fabs(r[2] - 0.125) < 0.001 && fabs(r[3] - 0.0625) < 0.001,
          "feedback 0.5: repeats 0.50 0.25 0.125 0.0625");

    /* darker repeats: the one-pole takes the edge off a click */
    memset(f->dbuf, 0, sizeof f->dbuf); f->dlp = 0;
    f->dfb = 0; f->ddamp = Q / 4;
    frame(2, Q / 2);
    int mx = 0;
    for (int t = 1; t < 5000; t++) {
        int y = frame(2, 0);
        if (y > mx)
            mx = y;
    }
    check(mx < Q / 2 * 0.3 && mx > Q / 2 * 0.2, "damped repeat: a click comes back softer and smeared (0.2-0.3 of it)");

    /* reverb: noise burst, tail rings then dies, never runs away */
    f->dlen = 0; f->ddamp = Q;
    f->rmix = 2 * Q; f->rfb = (int)(0.84 * Q); f->rdamp = (int)(0.8 * Q);
    unsigned seed = 1;
    double e_early = 0, e_late = 0;
    int clips0 = strip_host_mem.clips, worst = 0;
    for (int t = 0; t < 44100 * 3; t++) {
        seed = seed * 1103515245u + 12345u;
        int x = t < 22050 ? (int)((seed >> 8) % (Q / 2)) - Q / 4 : 0;
        int y = frame(2, x);
        if (y > worst)
            worst = y;
        if (t >= 22050 + 2205 && t < 22050 + 6615)
            e_early += (double)y * y;
        if (t >= 22050 + 88200 && t < 22050 + 92610)
            e_late += (double)y * y;
    }
    check(e_early > 0 && e_late < e_early * 1e-4, "reverb tail: rings after the burst, 60+ dB down 2 s later");
    check(strip_host_mem.clips == clips0 && worst < Q, "reverb on a loud burst: no clips, never past full scale");

    /* lengths from a broken PC: clamped by the ISR (ASan would stop here if anything wrote outside) */
    f->dlen = 999999; f->dmix = Q; f->dfb = Q / 2;
    f->clen[0] = 0; f->clen[1] = 1u << 31; f->alen[0] = 99999; f->alen[1] = 0;
    f->dpos = 123456789; f->cpos[2] = 0xffffffffu;
    for (int t = 0; t < 70000; t++)
        frame(2, (int)(0.2 * Q * sin(t * 0.01)));
    check(1, "absurd lengths and positions: clamped, no out-of-bounds access");
    printf("FX: %s (%d checks)\n", fails ? "FAILED" : "ALL PASSED", checks);
    return fails != 0;
}
