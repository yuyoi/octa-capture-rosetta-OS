/* Rosetta OCTA-CAPTURE DSP test 1: CPU meter + record-ch1 chopper.
 * Lives in the dead debug-overlay body of 0x30ddc8 (gates [0x4094e4]/[0x4094e8] are 0, never written).
 * Section .ra -> 0x30de0e (136 B), .rb -> 0x30de98 (292 B). See re/AUDIO_PATH.md.
 * Everything here runs only after the key scan (0x31bbf6):
 *   arm_stub  <- CALL at 0x31bd7a (mode 0 = normal boot only; update/recovery modes never call it)
 *   ui_hook   <- CALL at 0x359a16 (50 ms UI tick), does nothing extra until ARMED == MAGIC
 *   wrap48    <- installed into [0xff8010b4] by ui_hook, only while it holds the stock 48k chain 0xffa08054
 * Vars live in the overlay's own data (0x41d5e4, the unused "Free/Total" string; only 0x30ddc8 used it).
 */
        .set RVARS,          0x41d5e4      /* +0 ARMED, +4 LAST, +8 PER, +0xc BMAX, +0x10 FRAMES */
        .set MAGIC,      0x52535441    /* 'ATSR' (initial data there is "Free" = 0x65657246) */
        .set STOCK48,    0xffa08054
        .set CBPTR,      0xff8010b4
        .set GFX,        0x440944      /* graphics object pointer the overlay used */
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
        /* meter box: left, top, right, bottom (same layout as the overlay rect {4,192,87,208}) */
        .set BX0, 40
        .set BY0, 52
        .set BX1, 127
        .set BY1, 63

        .section .ra, "ax"
        .global wrap48, arm_stub

/* Called by the L1 audio worker: R0=in R1=out R2=usbplay, arg4 (rec) at caller [SP+0xc]. */
wrap48:
        LINK 0;
        [--SP] = (R7:7, P5:3);
        SP += -16;
        P3 = [FP + 0x14];               /* rec frame */
        P5.L = RVARS & 0xffff;  P5.H = RVARS >> 16;
        R7 = CYCLES;
        R3 = [P5 + 4];
        [P5 + 4] = R7;
        R3 = R7 - R3;
        [P5 + 8] = R3;                  /* period, core cycles */
        [SP + 0xc] = P3;
        P0.L = STOCK48 & 0xffff;  P0.H = STOCK48 >> 16;
        CALL (P0);                      /* stock 48k chain, untouched */
        R0 = [P5 + 0x10];               /* test B: chop record ch1 (~0.68 s on / off at 48k) */
        R0 += 1;
        [P5 + 0x10] = R0;
        CC = BITTST (R0, 15);
        IF !CC JUMP 1f;
        R0 = 0;
        [P3] = R0;
1:      R0 = CYCLES;
        R0 = R0 - R7;
        R1 = [P5 + 0xc];
        R0 = MAX (R0, R1);
        [P5 + 0xc] = R0;                /* busy max, core cycles */
        SP += 16;
        (R7:7, P5:3) = [SP++];
        UNLINK;
        RTS;

/* Replaces CALL 0x30dfdc at 0x31bd7a (mode-0 path, after the scan). R0 = 2 must survive. */
arm_stub:
        R1 = SYSCFG;
        BITSET (R1, 1);                 /* CCEN: start the cycle counter, keep SNEN */
        SYSCFG = R1;
        CSYNC;
        P1.L = RVARS & 0xffff;  P1.H = RVARS >> 16;
        R1 = 0;
        [P1 + 4] = R1;
        [P1 + 8] = R1;
        [P1 + 0xc] = R1;
        [P1 + 0x10] = R1;
        R1.L = MAGIC;  R1.H = MAGIC;
        [P1] = R1;
        JUMP.L f_orig_arm;              /* tail call, returns to 0x31bd7e */

        .section .rb, "ax"
        .global ui_hook
/* Replaces CALL 0x30ddc8 at 0x359a16 (UI tick). */
ui_hook:
        LINK 0x28;                      /* FP-0x20: text buffer (32 B), FP-0x28: rect (8 B) */
        [--SP] = (R7:6, P5:5);
        SP += -16;
        CALL f_overlay;                 /* stock call, overlays stay gated off */
        P5.L = RVARS & 0xffff;  P5.H = RVARS >> 16;
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
        [P1] = R1;                      /* (re)arm: task 10 rewrites it on rate changes */
2:      R6 = [P5 + 0xc];
        R7 = [P5 + 8];
        R0 = 0;
        [P5 + 0xc] = R0;
        STI R2;
        R0 = FP;                        /* sprintf(buf, "%2d/%2d", busy_max, period) */
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
        CALL f_font;                    /* font 0, as the stock heap overlay */
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

/* ---- hooks into stock code (each replaces exactly one stock instruction; builder checks the stock bytes) ---- */
        .section .h_arm, "ax"           /* 0x31bd7a: was CALL 0x30dfdc */
        CALL arm_stub;
        .section .h_ui, "ax"            /* 0x359a16: was CALL 0x30ddc8 */
        CALL ui_hook;
        .section .h_gb, "ax"            /* 0x30de0c: gate-B target -> plain exit (was R0 = [P5 + -0x8]) */
        .byte 0xfa, 0x2f                /* JUMP.S 0x30de00 (gas can't pcrel12 to an absolute; checked in the final image) */
        .section .h_ga, "ax"            /* 0x30de96: gate-A target -> back to the gate-B test (was R0.L = W[P5]) */
        .byte 0xb2, 0x2f                /* JUMP.S 0x30ddfa */
