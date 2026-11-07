# Power-on decision flow (booter + loader) - notes_safety_bootflow

TL;DR: booter and loader read NO buttons. At power-on the loader always runs main if main's
byte-sum is good. The button combo is read by MAIN itself, in a TOPPERS task. So a modified main
that has a good checksum but hangs before that task scans the keys CANNOT be recovered by holding
the combo. The only automatic way into the updater is a BAD main checksum (then the loader sets
R7=0x33 and runs the updater).

## Booter 0x20000000-0x20000FFF (CONFIRMED)
- 0x20000000: header word 0xAD7BD006, target 0x20000020, count 0x10, arg 0x10. This looks like a
  BF52x/BF54x boot-ROM block header (0xAD = header sign; FIRST|FINAL|IGNORE, so the ROM jumps to
  0x20000020 and runs the code in place). INFERRED.
- 0x20000020: SYSCFG=0x32, SP=0xffb01000, CALL 0x20000324 (meminit, table at 0x20000644: one
  record, zero 0x14 bytes at 0xff800000). Hang at 0x2000004c if it fails.
- It sets EVT15=0x200000f4 and runs RAISE 15/RTI. The IVG15 code at 0x200000f4 calls 0x20000110
  (EBIU_SDGCTL=0xe0911149, SDBCTL=1, SDRRC=0x5f9: SDRAM init). Then 0x20000100: CLI;
  P0=0x20001000; R7=0; JUMP (P0).
- No GPIO reads anywhere in the booter.

## Loader 0x20001000-0x20003FFF (CONFIRMED)
- 0x20001000: crt. CALL 0x20001a1c (meminit, table 0x200023a4, 10 records into L1, including
  0xffa08000..0xffa081f2 = L1 code). Then [0xff802360]=R7 (=0 from the booter). EVT15=0x200010f2,
  which runs CALL 0x2000159c (loader main). After that, 0x20001104: CLI; P0=0x300100;
  R7=[0xff802360]; JUMP (P0).
- 0x2000159c loader main:
  - PH13 output and set high (PORTHIO_DIR 0xffc01730, INEN 0x1740, PORTH_FER 0xffc03208,
    SET 0x1708). EBIU_AMGCTL=0xF.
  - LCD: 0x20001294 is the init. It bit-bangs on PORTG (0xffc01504/1508) with ST7565-style
    commands e3 a2 a0 c8 24 81 2a 2f 40 ac a4 af a6. 0x200013e4 draws the boot bitmap from
    0xff801b28. 0x20001450 refreshes the LCD.
  - Reads W[0x200200F8] (main ver) and W[0x200EFFFC] (main build). Then:
    - R7&0xF0==0x70: prints "SD BOOT: V%d.%02d(%04X)" from SDRAM W[0xFC]/W[0xFE] and sets
      state=2.
    - else: prints "MAIN V%d.%02d(%04X)" (shown BEFORE any checksum check).
  - 0x20001690 dispatch on R7=[0xff802360]:
    - 0x7x: state 2 (0x200016a0).
    - R7==0 (cold boot), 0x20001790: sums [0x20020000,0x200EFFFE) and compares with
      0x200EFFFE/F.
      - OK: state 1 (main), 0x200017de.
      - BAD, 0x200017e4: sums the updater [0x20004000,0x2001FFFE).
        - OK: state 0 and [0xff802360]=0x33 (0x20001834-38), so the updater runs with R7=0x33.
        - BAD: hangs at 0x2000183c (JUMP.S self).
    - R7!=0 and not 0x7x, 0x20001736: updater sum.
      - OK: state 0, 0x20001788.
      - BAD: hangs at 0x2000178c. Main is NOT used as a fallback.
  - States:
    - 0 and 1: P1=table[state] at 0xff801018 = {0x20004100, 0x20020100, 0x00300100,
      0x20000000}. Read the BE32 size at P1. If size<=0 (signed): hang at 0x20001702. Else
      CALL 0xffa0811c (R0=P1+4, size in R2).
    - 2: about 2000*400000 NOPs of delay, then jump 0x300100 with NO unpack. This runs whatever
      is already in SDRAM.
    - Any other value: hang at 0x20001730.
- LZSS decompressor: L1 0xffa0811c, flash copy at file 0x3238 (boot.dis 0x20003238).
  - Okumura LZSS: 4096-byte ring at 0xff800000 zero-filled, r=0xFEE, 12-bit offset, length
    nibble+3, flag byte LSB first.
  - dest P4=0x300100, end=0x300100+size.
  - No CRC or check of the output. The only integrity check is the 16-bit byte-sum over the
    compressed flash region.
- Stock sums verified: updater 0x7107, main 0xfc91, loader 0xbf12. Unpacked sizes: 0x1e070 and
  0xa2c9c (= main.bin size).

## Where the combo is read: MAIN (CONFIRMED)
- crt 0x300138 saves R7 into [0xffb00000]. get_R7=0x32a86a, set_R7=0x32a856.
- The reboot-to-loader path is 0x32a8cc, then 0x32a946 (CLI; JUMP 0x300208). 0x300208 jumps
  to 0x20001000 with R7=[0xffb00000].
- Task entry 0x31bba0 is in a TOPPERS task record at 0xff800550 (entry, 5, 0x1800). Its
  steps:
  - Init calls: 0x300d84, 0x30e5b4, 0x300be0, 0x325a76(0xa0), 0x329b0c(3).
  - Opens the "sw" driver (0x366aa4, name at 0x435d4c).
  - Reads 9 key bytes (0x3666d8 loop at 0x31bc0c-0x31bc22).
  - Checks the combo tables (byte indexes into the 9 keys):
    - 0x435d50 {6,0,0} -> set_R7(0x34). 0x435d60 {6,1,1} -> set_R7(0x33). Both lead to
      mode 3 = 0x31bdc4, then 0x32a8cc: reboot into the loader, which runs the updater.
    - 0x435d70 {4,2,0} -> mode 1. 0x435d80 {4,3,0} -> mode 2. 0x435d90 {0,1,1} -> mode 4.
      Otherwise mode 0 (normal). Dispatch table at 0x435db0.
- Updater 0x300898: (R7&0xF)==4 is "ALL" mode. So 0x34 = ALL, and 0x33 (combo or the
  auto-fallback) = normal.
- Main disables the watchdog (0x3297c4: WDOG_CTL=0x0AD0) and the loader never enables it, so
  a hung main stays hung.
- Main touches 0xffc038xx (USB) and PORTG/PORTH and MDMA at 0xffc00fxx. That means the CPU is
  really a BF52x (BF525/527), not a BF533. INFERRED from the MMR map.

## Answers
- (a) Main checksum wrong: the updater runs automatically (R7=0x33, normal mode), provided the
  updater checksum is good. If both are bad: hang at 0x2000183c.
- (b) Valid stream with a good checksum, but main crashes or hangs: the loader has already jumped
  to 0x300100 with no watchdog. Recovery only works if main reaches the key scan at 0x31bc0c in
  task 0x31bba0. If it doesn't, you need JTAG or an external flash write.
- (c) Updater checksum wrong: a cold boot still runs a good main. The combo (R7=0x33/0x34) hangs
  at 0x2000178c, and a power-cycle boots main again. If main is also bad: hang at 0x2000183c.
- Safety net for mods: leave the updater region byte-identical to stock. Do not touch code
  that runs before the key scan (crt, meminit, 0x32a880 init, kernel start, task 0x31bba0
  up to 0x31bd0a). Test the combo on stock first.
