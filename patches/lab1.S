/* Rosetta OCTA-CAPTURE lab1: CPU meter + Rosetta Lab SysEx backend (docs/LAB.md).
 *
 * .ra  0x30de0e (136 B, dead overlay body)  arm_stub
 * .rb  0x30de98 (292 B, dead overlay body)  ui_hook (meter on the LCD), midi_hook (ARMED gate stub)
 * .lab LAB_BASE (appended after the init table; the loader unpacks it with the rest of main):
 *      lab backend, wrap48 (strip + stock 48k chain + meter/chopper), strip.c
 *
 * Reachability (brick rule: nothing new may run before the key scan at 0x31bbf6):
 *   arm_stub  <- CALL at 0x31bd7a, mode-0 path only, after the scan. It sets ARMED.
 *   ui_hook   <- CALL at 0x359a16, UI tick (post-scan), gated on ARMED.
 *   midi_hook <- CALL at 0x36949a in the USB-MIDI dispatcher (may run before the scan!). Until ARMED it
 *                does exactly the two replaced instructions and returns; only then does it reach .lab.
 *   wrap48    <- installed into [0xff8010b4] by ui_hook (post-scan).
 * ARMED lives in .data (0x41d5e4, rebuilt by the init table at every boot), so it is never set before arm_stub.
 */
        .set VARS,       0x41d5e4      /* +0 ARMED, +4 LAST, +8 PER, +0xc BMAX, +0x10 FRAMES, +0x14 FLAGS */
        .set MAGIC,      0x52535441
        .set STOCK48,    0xffa08054
        .set CBPTR,      0xff8010b4
        .set GFX,        0x440944
        .set FMT,        0x43c160      /* stock "%2d/%2d" */
        .set f_orig_arm, 0x30dfdc
        .set f_overlay,  0x30ddc8
        .set f_sprintf,  0x324010
        .set f_gsave,    0x34df50
        .set f_grest,    0x34df02
        .set f_font,     0x34dfdc
        .set f_color,    0x34dee2
        .set f_erase,    0x34e6aa
        .set f_text,     0x34edd8
        .set BX0, 40
        .set BY0, 52
        .set BX1, 127
        .set BY1, 63
        /* C-side config: patches/lab_cfg.h */

/* ===================================================================== .ra */
        .section .ra, "ax"
        .global arm_stub
arm_stub:                               /* from 0x31bd7a, R0 = 2 must survive */
        [--SP] = RETS;
        [--SP] = R0;
        SP += -12;
        CALL _strip_init;               /* channel strip block in free L1 data: all off, then magic */
        CALL _page_install;             /* screen 18 -> STRIP page, SENS -> GAIN (page.c) */
        SP += 12;
        R0 = [SP++];
        RETS = [SP++];
        R1 = SYSCFG;
        BITSET (R1, 1);
        SYSCFG = R1;
        CSYNC;
        P1.L = VARS & 0xffff;  P1.H = VARS >> 16;
        R1 = 0;
        [P1 + 4] = R1;
        [P1 + 8] = R1;
        [P1 + 0xc] = R1;
        [P1 + 0x10] = R1;
        [P1 + 0x14] = R1;
        R1.L = MAGIC;  R1.H = MAGIC;
        [P1] = R1;
        JUMP.L f_orig_arm;

/* ===================================================================== .rb */
        .section .rb, "ax"
        .global ui_hook, midi_hook
ui_hook:
        LINK 0x28;
        [--SP] = (R7:6, P5:5);
        SP += -16;
        CALL f_overlay;
        P5.L = VARS & 0xffff;  P5.H = VARS >> 16;
        R0 = [P5];
        R1.L = MAGIC;  R1.H = MAGIC;
        CC = R0 == R1;
        IF !CC JUMP 9f;
        CLI R2;
        P1.L = CBPTR & 0xffff;  P1.H = CBPTR >> 16;
        R0 = [P1];
        R1.L = STOCK48 & 0xffff;  R1.H = STOCK48 >> 16;
        CC = R0 == R1;
        IF !CC JUMP 2f;
        R1.L = wrap48;  R1.H = wrap48;
        [P1] = R1;
2:      R6 = [P5 + 0xc];
        R7 = [P5 + 8];
        R0 = 0;
        [P5 + 0xc] = R0;
        STI R2;
        R0 = [P5 + 0x14];               /* FLAGS bit1: show the load numbers (octa_lab.py meter show); off by default */
        CC = BITTST (R0, 1);
        IF !CC JUMP 9f;
        R0 = FP;
        R0 += -0x20;
        R1.L = FMT & 0xffff;  R1.H = FMT >> 16;
        R2 = R6;
        [SP + 0xc] = R7;
        CALL f_sprintf;
        P5.L = GFX & 0xffff;  P5.H = GFX >> 16;
        R0 = [P5];
        CALL f_gsave;
        R0 = [P5];
        R1 = 0;
        CALL f_font;
        R7.L = 0;  R7.H = 0x0100;
        R6.L = 2;  R6.H = 0x0100;
        R0 = [P5];
        R1 = R7;
        R2 = R6;
        CALL f_color;
        R0 = BX0;  W[FP + -0x28] = R0;
        R0 = BY0;  W[FP + -0x26] = R0;
        R0 = BX1;  W[FP + -0x24] = R0;
        R0 = BY1;  W[FP + -0x22] = R0;
        R0 = [P5];
        R1 = FP;
        R1 += -0x28;
        CALL f_erase;
        R0 = [P5];
        R1 = R7;
        R2 = R6;
        CALL f_color;
        R0 = FP;
        R0 += -0x20;
        [SP + 0xc] = R0;
        R0 = [P5];
        R1 = BX0 + 1;
        R2 = BY0 + 2;
        CALL f_text;
        R0 = [P5];
        CALL f_grest;
9:      SP += 16;
        (R7:6, P5:5) = [SP++];
        UNLINK;
        RTS;

/* Replaces `R1 = [SP + 0x3c]; R0 = 0xf (X);` at 0x36949a in the USB-MIDI dispatcher 0x369468.
 * Live there: everything except R0/R1 (both rewritten by the replaced instructions). [SP+0x3c] = packet. */
midi_hook:
        [--SP] = ASTAT;
        [--SP] = P0;
        P0.L = VARS & 0xffff;  P0.H = VARS >> 16;
        R0 = [P0];
        R1.L = MAGIC;  R1.H = MAGIC;
        CC = R0 == R1;
        P0 = [SP++];
        IF CC JUMP 1f;
        ASTAT = [SP++];                 /* not armed: exactly the two replaced instructions */
        R1 = [SP + 0x3c];
        R0 = 0xf (X);
        RTS;
1:      ASTAT = [SP++];
        JUMP.L lab_entry;               /* SP, RETS and every other register still the dispatcher's */

/* ===================================================================== .lab (appended code) */
        .section .lab, "ax"
        .global lab_entry, wrap48
/* Reached from midi_hook only when ARMED (task 9, stack 0x400 with ~0x290 usable here: keep frames small).
 * The dispatcher needs R4, P3, P4, FP, SP and its stack slots [SP+0x34..0x3c] kept; R0-R3, R5-R7, P0-P2, P5,
 * CC are free (re-derived before use). Not ours: return with R1 = [SP+0x3c], R0 = 0xf. Ours: go to 0x369566. */
lab_entry:
        R0 = [SP + 0x3c];               /* packet, read before SP moves */
        [--SP] = RETS;
        SP += -12;
        CALL _lab_rx;                   /* GCC keeps R4-R7, P3-P5, FP; L0-L3 stay 0 */
        SP += 12;
        RETS = [SP++];
        CC = R0 == 0;
        R1 = [SP + 0x3c];
        IF CC JUMP 1f;
        P0.L = 0x9566;  P0.H = 0x36;    /* lab packet: skip Roland's listener loop (next packet), */
        R0 = 0xf (X);                   /* 0x369566 needs only R4, P4 and SP (all intact) */
        JUMP (P0);
1:      R0 = 0xf (X);                   /* not ours: exactly the two replaced instructions */
        RTS;

/* Per-sample wrapper around Roland's 48k chain (installed into [0xff8010b4] by ui_hook, post-scan; IVG10).
 * Channel strip first (strip.c, on in[] before the stock chain), then the stock chain, then the meter/chopper.
 * Stack: 52 B here; strip_pre's frame is gone before the stock chain runs, so the peak grows by 12 B only. */
wrap48:                                 /* R0=in R1=out R2=usbplay, arg4 rec at caller [SP+0xc] */
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
        CALL _strip_pre;                /* R0 = in; keeps R4-R7, P3-P5, FP; L0-L3 stay 0 */
        R0 = R4;
        R1 = R5;
        R2 = R6;
        [SP + 0xc] = P3;
        P0.L = STOCK48 & 0xffff;  P0.H = STOCK48 >> 16;
        CALL (P0);
        R0 = [P5 + 0x10];
        R0 += 1;
        [P5 + 0x10] = R0;
        R1 = [P5 + 0x14];               /* FLAGS bit0: record-ch1 chopper (off by default, lab: chop on) */
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

/* int bq_run(struct bq *s, int x): one biquad sample, exact accumulator math + error feedback (strip.c documents
 * the math; tests/test_strip_host.py runs the same model, `octa_lab.py strip bqtest` checks this code bit-exact).
 * struct bq: c[5] @0 (b0 b1 b2 -a1 -a2, Q30), x1 @20, x2 @24, y1 @28, y2 @32, e @36. Uses R0-R3, R7, P0, A0, A1. */
        .global _bq_run
_bq_run:
        [--SP] = R7;
        P0 = R0;
        R2 = [P0];
        A1 = R1.L * R2.L (FU);
        R2 = [P0 + 4];  R3 = [P0 + 20];
        A1 += R3.L * R2.L (FU);
        R2 = [P0 + 8];  R3 = [P0 + 24];
        A1 += R3.L * R2.L (FU);
        R2 = [P0 + 12]; R3 = [P0 + 28];
        A1 += R3.L * R2.L (FU);
        R2 = [P0 + 16]; R3 = [P0 + 32];
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
        R2 = [P0 + 8];  R3 = [P0 + 24];
        A1 += R3.H * R2.L (M, IS);
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

/* ---- fault catcher (lab v1.1) ----
 * u32 lab_guard(void (*fn)(void)): runs fn with _lab_exc armed. Returns 0, or 1 if fn faulted; then
 * _lab_fx[1..4] = SEQSTAT, DCPLB_FAULT_ADDR, DCPLB_STATUS, RETX of the fault.
 * _lab_exc is installed into EVT3 by lab_rx (post-scan only). When no lab access is armed it does exactly
 * what Roland's EVT3 (0xffa012d0) does: EMUEXCPT; RTX. */
        .global _lab_guard, _lab_exc, _lab_fx
_lab_guard:
        LINK 0;
        [--SP] = (R7:4, P5:3);
        P1.L = _lab_jb;  P1.H = _lab_jb;
        R1 = SP;
        [P1] = R1;
        R1 = FP;
        [P1 + 4] = R1;
        P1.L = _lab_fx;  P1.H = _lab_fx;
        R1 = 1;
        [P1] = R1;                      /* armed */
        SSYNC;
        P0 = R0;
        SP += -12;
        CALL (P0);
        SP += 12;
        P1.L = _lab_fx;  P1.H = _lab_fx;
        R1 = 0;
        [P1] = R1;
        R0 = 0;
2:      (R7:4, P5:3) = [SP++];
        UNLINK;
        RTS;
lab_recover:                            /* RTX lands here after a fault inside fn */
        P1.L = _lab_jb;  P1.H = _lab_jb;
        R1 = [P1];
        SP = R1;
        R1 = [P1 + 4];
        FP = R1;
        R0 = 0;
        CLI R2;                         /* anomaly 05000312: no interrupt while loading loop registers */
        LC0 = R0;                       /* kill any hardware loop fn was in */
        LC1 = R0;
        STI R2;
        L0 = 0; L1 = 0; L2 = 0; L3 = 0;
        R0 = 1;
        JUMP 2b;

_lab_exc:
        [--SP] = ASTAT;
        [--SP] = R0;
        [--SP] = P0;
        [--SP] = P1;
        P0.L = _lab_fx;  P0.H = _lab_fx;
        R0 = [P0];
        CC = R0 == 0;
        IF CC JUMP 9f;
        R0 = 0;
        [P0] = R0;                      /* disarm first: a fault in here must not loop back */
        R0 = SEQSTAT;
        [P0 + 4] = R0;
        P1.L = 0x0008;  P1.H = 0xffe0;  /* DCPLB_FAULT_ADDR */
        R0 = [P1];
        [P0 + 8] = R0;
        P1.L = 0x0004;  P1.H = 0xffe0;  /* DCPLB_STATUS */
        R0 = [P1];
        [P0 + 12] = R0;
        R0 = RETX;
        [P0 + 16] = R0;
        R0.L = lab_recover;  R0.H = lab_recover;
        RETX = R0;
        P1 = [SP++];
        P0 = [SP++];
        R0 = [SP++];
        ASTAT = [SP++];
        RTX;
9:      P1 = [SP++];
        P0 = [SP++];
        R0 = [SP++];
        ASTAT = [SP++];
        EMUEXCPT;                       /* stock behaviour */
        RTX;

        .align 4
_lab_fx:  .long 0, 0, 0, 0, 0
_lab_jb:  .long 0, 0

/* ---- hooks into stock code (each replaces exactly the stock bytes tools/build_dsp.py checks) ---- */
        .section .h_arm, "ax"           /* 0x31bd7a: was CALL 0x30dfdc */
        CALL arm_stub;
        .section .h_ui, "ax"            /* 0x359a16: was CALL 0x30ddc8 */
        CALL ui_hook;
        .section .h_gb, "ax"            /* 0x30de0c: gate-B target -> plain exit */
        .byte 0xfa, 0x2f                /* JUMP.S 0x30de00 */
        .section .h_ga, "ax"            /* 0x30de96: gate-A target -> back to the gate-B test */
        .byte 0xb2, 0x2f                /* JUMP.S 0x30ddfa */
        .section .h_midi, "ax"          /* 0x36949a: was R1 = [SP + 0x3c]; R0 = 0xf (X); */
        CALL midi_hook;
