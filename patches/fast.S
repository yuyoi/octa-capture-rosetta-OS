/* The per-sample audio path, linked to run from free L1 instruction memory (0xFFA04900, re/RAM_MAP.md).
 *
 * wrap48_fast is wrap48 from lab1.S with everything it calls next door: strip_pre (strip.c built with FAST_BLOB) and the
 * biquads here. Instruction memory costs no cache misses, which is where most of the ~200 cycles per biquad were going.
 * Going live is one word: [0xff8010b4] = wrap48_fast. Going back is the same word, and a power cycle undoes it anyway.
 * No absolute calls into SDRAM: the only way out is Roland's own chain, which is already in L1.
 */
        .set VARS,       0x41d5e4
        .set STOCK48,    0xffa08054

        .section .fast, "ax"
        .global wrap48_fast, _bq_run, _bq_run3, _bq_slim

wrap48_fast:                            /* R0=in R1=out R2=usbplay, arg4 rec at caller [SP+0xc] */
        LINK 0;
        [--SP] = (R7:4, P5:3);
        SP += -16;
        P3 = [FP + 0x14];
        R4 = R0;
        R5 = R1;
        R6 = R2;
        P5.L = VARS & 0xffff;  P5.H = VARS >> 16;
        R7 = CYCLES;
        R3 = [P5 + 4];
        [P5 + 4] = R7;
        R3 = R7 - R3;
        [P5 + 8] = R3;
        CALL _strip_pre;
        /* timing, all in the free scratch words at 0xff803fe0 (the install DMA uses them only before going live):
         * +0 sum of strip cycles, +4 samples, +8 sum of Roland-chain cycles, +0x10 proof of life (samples on this path) */
        R3 = CYCLES;
        R3 = R3 - R7;
        P0.L = 0x3fe0;  P0.H = 0xff80;
        R0 = [P0];
        R0 = R0 + R3;
        [P0] = R0;
        R0 = [P0 + 4];
        R0 += 1;
        [P0 + 4] = R0;
        R0 = [P0 + 0x10];
        R0 += 1;
        [P0 + 0x10] = R0;
        R0 = R4;
        R1 = R5;
        R2 = R6;
        R4 = CYCLES;                    /* R4 is free once the call's arguments are set, and survives the call */
        [SP + 0xc] = P3;
        P0.L = STOCK48 & 0xffff;  P0.H = STOCK48 >> 16;
        CALL (P0);
        R3 = CYCLES;
        R3 = R3 - R4;
        P0.L = 0x3fe0;  P0.H = 0xff80;
        R1 = [P0 + 8];
        R1 = R1 + R3;
        [P0 + 8] = R1;
        R0 = [P5 + 0x10];
        R0 += 1;
        [P5 + 0x10] = R0;
        R1 = [P5 + 0x14];
        CC = BITTST (R1, 0);
        IF !CC JUMP 1f;
        CC = BITTST (R0, 15);
        IF !CC JUMP 1f;
        R0 = 0;
        [P3] = R0;
1:      R0 = CYCLES;
        R0 = R0 - R7;
        R1 = [P5 + 0xc];
        R0 = MAX (R0, R1);
        [P5 + 0xc] = R0;
        SP += 16;
        (R7:4, P5:3) = [SP++];
        UNLINK;
        RTS;

/* ---- microbenchmarks, called by the lab (app/octa_probe.py micro): interrupts off, no loops, R0 = cycles taken ---- */
        .global _mb_nop, _mb_ld, _mb_ldst, _mb_br, _mb_brnp, _mb_call, _mb_bq, _mb_strip
_mb_nop:
        CLI R3;
        R1 = CYCLES;
        .rept 64
        NOP;
        .endr
        R2 = CYCLES;
        STI R3;
        R0 = R2 - R1;
        RTS;
_mb_ld:                                 /* R0 = address to read 64 times */
        P0 = R0;
        CLI R3;
        R1 = CYCLES;
        .rept 64
        R0 = [P0];
        .endr
        R2 = CYCLES;
        STI R3;
        R0 = R2 - R1;
        RTS;
_mb_ldst:                               /* R0 = address: 32 x load, then store next door */
        P0 = R0;
        CLI R3;
        R1 = CYCLES;
        .rept 32
        R0 = [P0];
        [P0 + 4] = R0;
        .endr
        R2 = CYCLES;
        STI R3;
        R0 = R2 - R1;
        RTS;
_mb_br:                                 /* 32 taken branches, predicted taken */
        CLI R3;
        R1 = CYCLES;
        .rept 32
        CC = R1 == R1;
        IF CC JUMP 1f (BP);
1:
        .endr
        R2 = CYCLES;
        STI R3;
        R0 = R2 - R1;
        RTS;
_mb_brnp:                               /* 32 taken branches, predicted not taken (what gcc's skipped blocks look like) */
        CLI R3;
        R1 = CYCLES;
        .rept 32
        CC = R1 == R1;
        IF CC JUMP 1f;
1:
        .endr
        R2 = CYCLES;
        STI R3;
        R0 = R2 - R1;
        RTS;
_mb_call:                               /* 16 calls of an empty function */
        [--SP] = RETS;
        CLI R3;
        R1 = CYCLES;
        .rept 16
        CALL _mb_ret;
        .endr
        R2 = CYCLES;
        STI R3;
        RETS = [SP++];
        R0 = R2 - R1;
        RTS;
_mb_ret:
        RTS;
_mb_bq:                                 /* R0 = a struct bq: 8 calls of bq_run on it */
        [--SP] = RETS;
        [--SP] = (R7:4, P5:5);
        P5 = R0;
        CLI R6;
        R5 = CYCLES;
        .rept 8
        R0 = P5;
        R1 = 0x100;
        CALL _bq_run;
        .endr
        R4 = CYCLES;
        STI R6;
        R0 = R4 - R5;
        (R7:4, P5:5) = [SP++];
        RETS = [SP++];
        RTS;
_mb_strip:                              /* R0 = a 12-word input frame: one strip_pre, as the ISR would call it */
        [--SP] = RETS;
        [--SP] = (R7:4);
        CLI R6;
        R5 = CYCLES;
        CALL _strip_pre;
        R4 = CYCLES;
        STI R6;
        R0 = R4 - R5;
        (R7:4) = [SP++];
        RETS = [SP++];
        RTS;

/* int bq_fast(struct bq *s, int x): bq_run's exact sums, reordered so the loads ride along with the multiplies and the
 * high x high products use the second multiplier. Integer sums are exact in any order, so the output is bit-for-bit
 * bq_run's (the selftest checks that on the box). struct bq: c[5] @0, x1 @20, x2 @24, y1 @28, y2 @32, e @36. */
        .global _bq_fast, _mb_bqf
_bq_fast:
        P0 = R0;
        I0 = R0;                        /* walks c0..c4 for the low halves */
        I1 = R0;                        /* walks them again for the rest */
        R0 = 1;
        R2 = [I0++];                                            /* c0 */
        A1 = R1.L * R2.L (FU) || R2 = [I0++] || R3 = [P0 + 20];  /* x  c0 ; c1 x1 */
        A1 += R3.L * R2.L (FU) || R2 = [I0++] || R3 = [P0 + 24]; /* x1 c1 ; c2 x2 */
        A1 += R3.L * R2.L (FU) || R2 = [I0++] || R3 = [P0 + 28]; /* x2 c2 ; c3 y1 */
        A1 += R3.L * R2.L (FU) || R2 = [I0++] || R3 = [P0 + 32]; /* y1 c3 ; c4 y2 */
        A1 += R3.L * R2.L (FU);                                  /* y2 c4 */
        A1 = A1 >> 16 || R2 = [I1++] || R3 = [P0 + 20];          /* c0 x1 */
        A1 += R1.H * R2.L (M), A0 = R1.H * R2.H (IS);
        A1 += R2.H * R1.L (M, IS) || R2 = [I1++] || NOP;         /* c1 */
        A1 += R3.H * R2.L (M), A0 += R3.H * R2.H (IS);
        A1 += R2.H * R3.L (M, IS) || R2 = [I1++] || R3 = [P0 + 24];  /* c2 x2 */
        A1 += R3.H * R2.L (M), A0 += R3.H * R2.H (IS);
        A1 += R2.H * R3.L (M, IS) || R2 = [I1++] || R3 = [P0 + 28];  /* c3 y1 */
        A1 += R3.H * R2.L (M), A0 += R3.H * R2.H (IS);
        A1 += R2.H * R3.L (M, IS) || R2 = [I1++] || R3 = [P0 + 32];  /* c4 y2 */
        A1 += R3.H * R2.L (M), A0 += R3.H * R2.H (IS);
        A1 += R2.H * R3.L (M, IS) || R2 = [P0 + 36] || NOP;      /* e */
        A1 += R2.L * R0.L (FU);
        R2 = A1.W;
        R3 = 0x3fff (Z);
        R2 = R2 & R3;
        A1 = A1 >>> 14 || [P0 + 36] = R2 || NOP;
        A0 = A0 << 2;
        R0 = (A0 += A1) || R2 = [P0 + 20] || NOP;                /* old x1 */
        R3.L = 0xffff;  R3.H = 0x3fff;
        R0 = MIN (R0, R3);
        R3 = ~R3;
        R0 = MAX (R0, R3);
        [P0 + 24] = R2;
        [P0 + 20] = R1;
        R2 = [P0 + 28];
        [P0 + 32] = R2;
        [P0 + 28] = R0;
        RTS;

_mb_bqf:                                /* R0 = a struct bq: 8 calls of bq_fast on it */
        [--SP] = RETS;
        [--SP] = (R7:4, P5:5);
        P5 = R0;
        CLI R6;
        R5 = CYCLES;
        .rept 8
        R0 = P5;
        R1 = 0x100;
        CALL _bq_fast;
        .endr
        R4 = CYCLES;
        STI R6;
        R0 = R4 - R5;
        (R7:4, P5:5) = [SP++];
        RETS = [SP++];
        RTS;

/* ---- timing Roland's comp (0xffa098ea, called from 0xffa0832a in their 48k chain) ----
 * The CALL there is pointed here for a measurement only. Nothing touches SP: the comp reads a stack argument relative to
 * its caller's SP. Only P1 (scratch for any call) is used across the jump; R2, R3 and ASTAT are saved in memory and put
 * back. Return goes through RETS -> our post label -> Roland's return address. */
        .set CT, 0xff803f40             /* +0 R3, +4 return address, +8 start, +0xc cycle sum, +0x10 R2, +0x14 ASTAT, +0x18 calls,
                                           +0x1c/+0x20 in[0]/in[1] before, +0x24 the in[] pointer, +0x28 capture index */
        .set CAP, 0x3c0000              /* 128 x {in0 before, in1 before, in0 after, in1 after}: free SDRAM */
        .global _comp_timed
_comp_timed:
        P1.L = CT & 0xffff;  P1.H = CT >> 16;
        [P1] = R3;
        R3 = RETS;
        [P1 + 4] = R3;
        [P1 + 0x24] = R0;               /* the comp is called as comp(in, in, ...): R0 is the frame */
        P2 = R0;
        R3 = [P2];
        [P1 + 0x1c] = R3;
        R3 = [P2 + 4];
        [P1 + 0x20] = R3;
        R3 = CYCLES;
        [P1 + 8] = R3;
        R3 = [P1];
        P1.L = _comp_timed_post;  P1.H = _comp_timed_post;
        RETS = P1;
        P1.L = 0x98ea;  P1.H = 0xffa0;
        JUMP (P1);
_comp_timed_post:
        P1.L = CT & 0xffff;  P1.H = CT >> 16;
        [P1] = R3;
        [P1 + 0x10] = R2;
        R3 = ASTAT;
        [P1 + 0x14] = R3;
        R3 = CYCLES;
        R2 = [P1 + 8];
        R3 = R3 - R2;
        R2 = [P1 + 0xc];
        R2 = R2 + R3;
        [P1 + 0xc] = R2;
        R2 = [P1 + 0x18];
        R2 += 1;
        [P1 + 0x18] = R2;
        R2 = [P1 + 0x28];               /* capture: one shot of 128 samples, re-armed by zeroing the index */
        R3 = 128;
        CC = R2 < R3;
        IF !CC JUMP 2f;
        R3 = R2;
        R3 += 1;
        [P1 + 0x28] = R3;
        R2 <<= 4;
        R3.L = CAP & 0xffff;  R3.H = CAP >> 16;
        R2 = R2 + R3;
        P0 = R2;
        P2 = [P1 + 0x24];
        R3 = [P1 + 0x1c];
        [P0] = R3;
        R3 = [P1 + 0x20];
        [P0 + 4] = R3;
        R3 = [P2];
        [P0 + 8] = R3;
        R3 = [P2 + 4];
        [P0 + 12] = R3;
2:      R3 = [P1 + 0x14];
        ASTAT = R3;
        R2 = [P1 + 0x10];
        R3 = [P1];
        P1 = [P1 + 4];
        JUMP (P1);

/* ---- skipping Roland's comp when all 8 comp switches are off ----
 * With every switch off their comp passes each sample through unchanged (measured: same sample, 128/128), so not calling
 * it gives the same audio. The 8 switches are read every sample straight from Roland's parameter store, the way their own
 * getter 0x3097e4 does ([[0x41d024 + 4 ch]] + 4 * 10), so switching a comp on takes effect on the next sample. No stack
 * use; R3 and ASTAT are saved and put back; P0-P2 are scratch for any call. */
        .set CM, 0xff803f70             /* +0 R3, +4 ASTAT, +8 samples skipped, +0xc samples run */
        .global _comp_maybe
_comp_maybe:
        P1.L = CM & 0xffff;  P1.H = CM >> 16;
        [P1] = R3;
        R3 = ASTAT;
        [P1 + 4] = R3;
        P0.L = 0xd024;  P0.H = 0x41;
        P2 = [P0 + 0];
        P2 = [P2];
        R3 = [P2 + 40];
        CC = R3 == 0;
        IF !CC JUMP 3f;
        P2 = [P0 + 4];
        P2 = [P2];
        R3 = [P2 + 40];
        CC = R3 == 0;
        IF !CC JUMP 3f;
        P2 = [P0 + 8];
        P2 = [P2];
        R3 = [P2 + 40];
        CC = R3 == 0;
        IF !CC JUMP 3f;
        P2 = [P0 + 12];
        P2 = [P2];
        R3 = [P2 + 40];
        CC = R3 == 0;
        IF !CC JUMP 3f;
        P2 = [P0 + 16];
        P2 = [P2];
        R3 = [P2 + 40];
        CC = R3 == 0;
        IF !CC JUMP 3f;
        P2 = [P0 + 20];
        P2 = [P2];
        R3 = [P2 + 40];
        CC = R3 == 0;
        IF !CC JUMP 3f;
        P2 = [P0 + 24];
        P2 = [P2];
        R3 = [P2 + 40];
        CC = R3 == 0;
        IF !CC JUMP 3f;
        P2 = [P0 + 28];
        P2 = [P2];
        R3 = [P2 + 40];
        CC = R3 == 0;
        IF !CC JUMP 3f;
        R3 = [P1 + 8];                  /* all off: their comp would hand every sample back unchanged */
        R3 += 1;
        [P1 + 8] = R3;
        R3 = [P1 + 4];
        ASTAT = R3;
        R3 = [P1];
        RTS;
3:      R3 = [P1 + 0xc];                /* at least one on: run it, returning straight to Roland's chain */
        R3 += 1;
        [P1 + 0xc] = R3;
        R3 = [P1 + 4];
        ASTAT = R3;
        R3 = [P1];
        P1.L = 0x98ea;  P1.H = 0xffa0;
        JUMP (P1);

        .include "fast_bq.inc"          /* _bq_run, taken from lab1.S by build_fast.py so there is only one copy of it */

/* int bq_slim(struct bq *s, int x): bq_run without the multiplies by the low halves of b0, b1, b2 (14 multiplies, not 20).
 * SLIM mode sends b0-b2 with those halves zero, so the two give the same output. The feedback taps keep full precision. */
_bq_slim:
        [--SP] = R7;
        P0 = R0;
        R2 = [P0 + 12]; R3 = [P0 + 28];
        A1 = R3.L * R2.L (FU);
        R2 = [P0 + 16]; R3 = [P0 + 32];
        A1 += R3.L * R2.L (FU);
        A1 = A1 >> 16;
        R2 = [P0];
        A1 += R2.H * R1.L (M, IS);
        A0 = R1.H * R2.H (IS);
        R2 = [P0 + 4];  R3 = [P0 + 20];
        A1 += R2.H * R3.L (M, IS);
        A0 += R3.H * R2.H (IS);
        R2 = [P0 + 8];  R3 = [P0 + 24];
        A1 += R2.H * R3.L (M, IS);
        A0 += R3.H * R2.H (IS);
        R2 = [P0 + 12]; R3 = [P0 + 28];
        A1 += R3.H * R2.L (M, IS);
        A1 += R2.H * R3.L (M, IS);
        A0 += R3.H * R2.H (IS);
        R2 = [P0 + 16]; R3 = [P0 + 32];
        A1 += R3.H * R2.L (M, IS);
        A1 += R2.H * R3.L (M, IS);
        A0 += R3.H * R2.H (IS);
        R2 = [P0 + 36];
        R7 = 1;
        A1 += R2.L * R7.L (FU);
        R2 = A1.W;
        R3 = 0x3fff (Z);
        R2 = R2 & R3;
        [P0 + 36] = R2;
        A1 = A1 >>> 14;
        A0 = A0 << 2;
        R0 = (A0 += A1);
        R2.L = 0xffff;  R2.H = 0x3fff;
        R0 = MIN (R0, R2);
        R2 = ~R2;
        R0 = MAX (R0, R2);
        R2 = [P0 + 20]; [P0 + 24] = R2;
        [P0 + 20] = R1;
        R2 = [P0 + 28]; [P0 + 32] = R2;
        [P0 + 28] = R0;
        R7 = [SP++];
        RTS;

/* int bq_run3(struct bq *s, int x): the same sum as bq_run for a stage with c[2] = c[4] = 0 (the tube's first-order DC
 * blocker, rbj 'dc' in octa_lab.py), so the output is bit-for-bit the same with 8 of the 20 multiplies gone. x2/y2 are not
 * kept up to date, because those coefficients are zero and nothing reads them. */
_bq_run3:
        [--SP] = R7;
        P0 = R0;
        R2 = [P0];
        A1 = R1.L * R2.L (FU);
        R2 = [P0 + 4];  R3 = [P0 + 20];
        A1 += R3.L * R2.L (FU);
        R2 = [P0 + 12]; R3 = [P0 + 28];
        A1 += R3.L * R2.L (FU);
        A1 = A1 >> 16;
        R2 = [P0];
        A1 += R1.H * R2.L (M, IS);
        A1 += R2.H * R1.L (M, IS);
        A0 = R1.H * R2.H (IS);
        R2 = [P0 + 4];  R3 = [P0 + 20];
        A1 += R3.H * R2.L (M, IS);
        A1 += R2.H * R3.L (M, IS);
        A0 += R3.H * R2.H (IS);
        R2 = [P0 + 12]; R3 = [P0 + 28];
        A1 += R3.H * R2.L (M, IS);
        A1 += R2.H * R3.L (M, IS);
        A0 += R3.H * R2.H (IS);
        R2 = [P0 + 36];
        R7 = 1;
        A1 += R2.L * R7.L (FU);
        R2 = A1.W;
        R3 = 0x3fff (Z);
        R2 = R2 & R3;
        [P0 + 36] = R2;
        A1 = A1 >>> 14;
        A0 = A0 << 2;
        R0 = (A0 += A1);
        R2.L = 0xffff;  R2.H = 0x3fff;
        R0 = MIN (R0, R2);
        R2 = ~R2;
        R0 = MAX (R0, R2);
        [P0 + 20] = R1;
        [P0 + 28] = R0;
        R7 = [SP++];
        RTS;
