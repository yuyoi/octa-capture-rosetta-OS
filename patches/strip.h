/* Channel strip block layout, shared by strip.c (DSP side) and page.c (STRIP page). docs/STRIP.md */
#ifndef STRIP_H
#define STRIP_H
typedef unsigned int u32;

#define MAGIC 0x53545250u               /* 'STRP' */
#define VER 4                           /* 3 = VER 2 + the amp block after ch[] (off[19] = its offset);
                                           4 = + F_AMP_HARD (same layout) */
#define NCH 8
#define NBQ 5                           /* 0 HPF, 1 low shelf, 2 peak, 3 high shelf, 4 tube DC blocker */
#define NAMP 6                          /* amp biquads, mailbox slots NBQ..NBQ+5: 0 before stage 1, 1 between the stages,
                                           2-5 speaker */
#define ONE (1 << 27)                   /* 1.0 = full scale */
#define YMAX (1 << 30)                  /* biquad output limit, 8 FS: no product can overflow (|c| < 2) */
#define OMAX (1 << 29)                  /* strip output limit, 4 FS (+12 dB) */

enum { F_HPF = 1, F_LOW = 2, F_PEAK = 4, F_HIGH = 8, F_TUBE = 16, F_COMP = 32, F_GAIN = 64, F_AMP = 128,
       F_SLIM_PEAK = 256, F_SLIM_HIGH = 512, F_COMP_DIV = 1024,     /* SLIM mode, fast path only (see strip.c) */
       F_AMP_HARD = 2048,                                             /* amp stages clip hard (sharp corner), not the curve */
       F_FX = 4096,                                                   /* this input feeds the echo + reverb (one per box) */
       F_GATE = 8192 };                                               /* noise gate (struct gate) */

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

/* One guitar amp per box, on input `ch`: replaces tube and comp there. Gain stages sit before the strip's low/peak/high
 * (the tone stack), the speaker after them. */
struct amp {
    u32 ch;                             /* input it runs on, anything >= NCH = none */
    int d1, b1, c1;                     /* stage 1: Q27 drive, bias, curve(bias) */
    int d2, b2, c2;                     /* stage 2 */
    int out;                            /* Q27 output level, after the speaker */
    struct bq bq[NAMP];
};

struct strip {
    u32 magic, ver, nch, chan_size;
    u32 off[20];                        /* layout for the PC, see strip_init */
    u32 clips;                          /* output limiter hits */
    u32 mb_seq, mb_done, mb_target;     /* target = ch << 4 | slot, + 0x100 = also clear that biquad's state */
    int mb_c[5];
    struct chan ch[NCH];
    struct amp amp;
};

#define STRIP_ADDR 0xFF8034C0u          /* free L1 data A tail */

/* SLIM comp: the level detector and gain curve run every COMP_DIV samples, staggered across the inputs (start n = ch), and
 * the gain is applied every sample. Kept outside struct strip so its layout stays the same. */
#define COMP_DIV 8
struct slimc {
    int pk;                             /* highest level since the last update */
    int n;                              /* samples until the next one */
    int lin;                            /* Q27 gain now */
};
#define SLIM_ADDR 0xFF803EA0u           /* 8 x 12 B, between the end of struct strip and the DMA scratch at 0xFF803F00 */

/* Echo + reverb on one input (the one with F_FX), after its whole strip and amp. Dry stays the same sample; only the
 * repeats and the tail come later. Buffers are 16-bit (analog-delay grain, -96 dB), in free SDRAM (re/RAM_MAP.md:
 * 0x36E66C-0x3FFFFF; our lab code ends 0x3AEF5C, uploads 0x3B0000-0x3B2FFF, scratch 0x3E0000+). The PC writes the
 * single words; the ISR bounds-checks every length itself, so no value can make it write outside these buffers. */
#define FX_ADDR 0x003B4000u
#define FX_MAGIC 0x31584652u            /* 'RFX1' */
#define FX_DMAX 60000                   /* echo up to 1.36 s at 44.1 kHz, 1.25 s at 48 */
#define FX_NC 4                         /* reverb: 4 damped combs into 2 allpasses */
#define FX_CMAX 1700
#define FX_NA 2
#define FX_AMAX 700
struct fx {
    u32 magic;
    u32 dlen;                           /* echo time in samples, 0 = no echo */
    int dfb, dmix, ddamp;               /* Q27: feedback, echo level, repeats' low-pass coefficient (1.0 = bright) */
    int rmix, rfb, rdamp;               /* Q27: reverb level (0 = off), comb feedback (size), comb low-pass coefficient */
    u32 clen[FX_NC], alen[FX_NA];       /* lengths in samples (the PC scales them to the rate) */
    u32 dpos;                           /* state from here on */
    int dlp;
    u32 cpos[FX_NC];
    int clp[FX_NC];
    u32 apos[FX_NA];
    short dbuf[FX_DMAX];
    short cbuf[FX_NC][FX_CMAX];
    short abuf[FX_NA][FX_AMAX];
};
#define FX_PARAM_WORDS 13               /* dlen .. alen[1]: what the PC sets and a scene keeps */

/* Noise gate per input: listens to the input as it comes in (before gain, EQ, amp), so a fuzz's own hiss can't hold it
 * open, and turns the input down after its whole strip/amp but BEFORE the echo/reverb, so tails ring out. No lookahead:
 * it opens on the same sample the note arrives, in `att` (a fraction of a millisecond), and adds no delay. In SDRAM
 * after struct fx; open/close are linear Q27 peak levels (close below open = hysteresis), hold in samples, att/rel Q30
 * one-pole steps per sample. */
#define GATE_ADDR 0x003D6000u
#define GATE_MAGIC 0x31544147u          /* 'GAT1' */
struct gate_ch {
    int open, close;                    /* Q27 thresholds */
    int env, g;                         /* state: peak follower, gain (Q27, 0 = shut) */
    u32 cnt, is_open;
};
struct gate {
    u32 magic;
    int hold, att, rel;                 /* shared by all inputs */
    struct gate_ch ch[NCH];
};
#endif
