/* Channel strip block layout, shared by strip.c (DSP side) and page.c (STRIP page). docs/STRIP.md */
#ifndef STRIP_H
#define STRIP_H
typedef unsigned int u32;

#define MAGIC 0x53545250u               /* 'STRP' */
#define VER 2
#define NCH 8
#define NBQ 5                           /* 0 HPF, 1 low shelf, 2 peak, 3 high shelf, 4 tube DC blocker */
#define ONE (1 << 27)                   /* 1.0 = full scale */
#define YMAX (1 << 30)                  /* biquad output limit, 8 FS: no product can overflow (|c| < 2) */
#define OMAX (1 << 29)                  /* strip output limit, 4 FS (+12 dB) */

enum { F_HPF = 1, F_LOW = 2, F_PEAK = 4, F_HIGH = 8, F_TUBE = 16, F_COMP = 32, F_GAIN = 64 };

struct bq {
    int c[5];                           /* b0 b1 b2 -a1 -a2, Q30, a0 = 1 */
    int x1, x2, y1, y2;                 /* Q27 */
    u32 e;                              /* error feedback: carried fraction, 2^-14 of an output LSB */
};

struct chan {
    u32 flags;                          /* F_* */
    int gin, gout;                      /* Q27 linear gains (F_GAIN) */
    struct bq bq[NBQ];
    int drive, bias, c0, tout;          /* tube: Q27 drive (<16), bias, curve(bias), output gain */
    int thr, khalf, inv2k, slope;       /* comp: threshold (Q24 log2 re FS), knee/2 (Q24), 1/(2 knee) (Q24),
                                           1 - 1/ratio (Q30) */
    int atk, rel, makeup;               /* one-pole coefs (Q30), makeup (Q24 log2) */
    int g, grmax;                       /* gain reduction now / max since the PC cleared it (Q24 log2, >= 0) */
};

struct strip {
    u32 magic, ver, nch, chan_size;
    u32 off[20];                        /* layout for the PC, see strip_init */
    u32 clips;                          /* output limiter hits */
    u32 mb_seq, mb_done, mb_target;     /* target = ch << 4 | slot, + 0x100 = also clear that biquad's state */
    int mb_c[5];
    struct chan ch[NCH];
};

#define STRIP_ADDR 0xFF8034C0u          /* free L1 data A tail */
#endif
