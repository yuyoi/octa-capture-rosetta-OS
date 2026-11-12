/* Host test of the noise gate (strip.c gate_run), ASan/UBSan build. */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "strip.h"

extern struct strip strip_host_mem;
extern struct gate strip_host_gate;
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
static unsigned seed = 7;
static int noise(double db)                     /* white noise at a peak level */
{
    seed = seed * 1103515245u + 12345u;
    double a = pow(10, db / 20) * Q;
    return (int)(((int)((seed >> 8) % 20001) - 10000) / 10000.0 * a);
}

static int frame(int x)
{
    int in[10] = { 0 };
    in[0] = x;
    strip_pre(in);
    return in[0];
}

int main(void)
{
    strip_init();
    struct gate *G = &strip_host_gate;
    check(G->magic == 0x31544147u, "gate block up");
    check(sizeof(struct fx) + 0x3B4000u <= 0x3D6000u && 0x3D6000u + sizeof(struct gate) <= 0x3E0000u,
          "FX and gate blocks fit 0x3B4000-0x3DFFFF, no overlap");
    strip_host_mem.ch[0].flags = F_GATE;
    /* playing above the threshold: passes untouched */
    int same = 1;
    for (int t = 0; t < 4410; t++) {
        int x = (int)(0.3 * Q * sin(t * 0.05));
        same &= frame(x) == x;
    }
    check(same, "note above threshold: bit-identical (gate open, gain exactly 1)");
    /* stop playing, hiss at -55 dBFS stays: after hold (60 ms) + release the hiss is gone */
    double e_end = 0;
    int max_step = 0, prev = 0;
    for (int t = 0; t < 44100; t++) {
        int y = frame(noise(-55));
        if (t > 30000)
            e_end = fmax(e_end, fabs((double)y));
        (void)prev;
    }
    check(e_end < Q * pow(10, -90 / 20.0), "hiss at -55 dBFS after the note: below -90 dBFS within 0.7 s");
    /* hiss between close (-51) and open (-45): stays shut (hysteresis) */
    int leaks = 0;
    for (int t = 0; t < 22050; t++)
        leaks += fabs((double)frame(noise(-48))) > Q * pow(10, -80 / 20.0);
    check(leaks == 0, "hiss at -48 dBFS (between close and open): stays shut");
    /* a pick attack: open fast, without a click */
    int t_open = -1;
    prev = strip_host_gate.ch[0].g;
    for (int t = 0; t < 2000; t++) {
        int x = (int)(0.25 * Q * sin(t * 0.07));
        int y = frame(x);
        int g = strip_host_gate.ch[0].g;
        if (t_open < 0 && g > Q * 0.9)
            t_open = t;
        int step = g - prev;
        if (step > max_step)
            max_step = step;
        prev = g;
    }
    check(t_open >= 0 && t_open < 44, "note after silence: gain past 0.9 within 1 ms");
    check(max_step < Q / 10, "opening ramps (no single-sample jump): no click");
    /* echo tail rings on after the gate shuts */
    struct fx *f = &strip_host_fx;
    f->dlen = 22050; f->dfb = Q / 2; f->dmix = Q; f->ddamp = Q;
    strip_host_mem.ch[0].flags = F_GATE | F_FX;
    for (int t = 0; t < 2205; t++)
        frame((int)(0.3 * Q * sin(t * 0.05)));
    double tail = 0;
    for (int t = 0; t < 44100; t++) {
        int y = frame(noise(-60));
        if (t > 22050 - 2205 && t < 22050 + 2205)
            tail = fmax(tail, fabs((double)y));
    }
    check(strip_host_gate.ch[0].is_open == 0 && tail > 0.1 * Q, "gate shut, the echo of the last note still rings (gate is before FX)");
    printf("GATE: %s (%d checks)\n", fails ? "FAILED" : "ALL PASSED", checks);
    return fails != 0;
}
