# OCTA-CAPTURE audio path + first DSP patches (AUDIO_PATH.md)

Sources: 4 adversarially re-checked maps (irq-dma, dsp-chain, timing-lcd, boot-space), re/main.dis,
the L1 code rebuilt from the init table (tools/build_splash_main.py parse()/run()), stock/main.bin.
Every claim is tagged CONFIRMED (seen in code, address given) or INFERRED.
Addresses are runtime addresses. File offset for .text = addr - 0x300100.

## 1. TL;DR

- The audio DSP is one per-sample C callback. The IVG10 ISR (L1 0xffa013bc) calls the worker
  0xffa02324, which calls `[0xff8010b4](in, out, usbplay, rec)`. The stock 48k chain is
  trim -> meters -> polarity -> IIR low-cut -> COMP -> mix/reverb -> rec[0..9] = in[0..9].
  The USB record frame is 0xff802c84. (CONFIRMED)
- Safe hook: after the scan, swap the pointer at 0xff8010b4 from 0xffa08054 to our wrapper,
  inside CLI/STI. The swap runs from the UI tick (0x359a16), which only runs after 0x31bbf6.
  The wrapper calls the stock callback, then edits or times the frame. (CONFIRMED post-scan path)
- No init-table edit, no image-size change, no L1 change. The code goes into a dead 428-byte
  debug-overlay area in .text (0x30de0e-0x30dfbb), reached only through two gate words that are
  0 and never written. The only pre-scan change is the image checksum, which is already
  proven on hardware. (CONFIRMED)
- Plan A (load meter): CYCLES around the stock callback, plus the time between calls. Shown
  as raw "busy/period" cycles; this needs no divide, and the period also gives CLKIN.
  Plan B (audible): rec[0] >>>= 1 after the stock callback, so USB record ch1 = -6 dB and the
  monitor is untouched. Both are buildable now. (INFERRED: the per-channel mapping and the
  LCD visibility still need one hardware test.)
- Never touch: crt/meminit, the init table, the L1 code (ISR, worker, callbacks), table 0x436214
  in the file, mode table 0x435db0 (FLASH_SAFETY rule 8), task bodies before 0x31bd22.
  Audio may start BEFORE the scan. That is unresolved, so treat it as possible. (INFERRED)

## 2. Audio path map

### 2.1 Interrupt routing (CONFIRMED)
- SIC_IAR2 0xffc00118 = 0x65663333 at 0x30e48c-0x30e49e. IDs 16-19 (DMA3-6 = SPORT0/1 RX/TX)
  go to IVG10.
- EVT10 = 0xffa013bc. It is written only by the crt at 0x3001a8 (R0.L built at 0x3001a4). No
  other code touches the EVT table.
- Only ID16 is unmasked (ID18 at 192k), by ena_int 0xffa01654 called from the SPORT enable
  0xffa01bc8 (0xffa01ca0-0xffa01ca6). 0xffa01bc8 has one caller, 0x363822, in the SPORT start
  0x363574, which is reached only from audio_serial ioctl cmd 0 (0x3663b4).
- Crt sets SYSCFG |= 4 (SNEN) at 0x300142-0x300148. CCEN (bit1) is never set, and no .text or
  L1 code reads CYCLES.

### 2.2 IVG10 ISR 0xffa013bc (CONFIRMED)
- W[PORTHIO_SET 0xffc01708] = 8 (PH3 high). It pushes R0/P0/old SP on the interrupted stack,
  sets SP = 0xffb00344 (absolute), then `[--SP] = RETI`, which enables nesting so IVG7-9 can
  pre-empt it. Full context save (184 B incl. arg area), L0-L3 = 0.
- Loop: CALL 0xffa02324 while DMA3 IRQ_STATUS 0xffc00ce8 bit0 is set (0xffa01422-0xffa01438).
  The loop always polls DMA3, even at 192k.
- Exit: restore, W[PORTHIO_CLEAR 0xffc01704] = 8, RTI 0xffa0148e.
- The IVG10 stack is 640 B (0xffb00344 down to 0xffb000c4; the IVG9 stack sits below).
  The stock path uses about 300-330 B before the comp internals and the reverb callees.
  Budget at most 64 B for our wrapper. (INFERRED arithmetic)
- Overrun hazard: SNEN = 1 + RETI pushed + absolute SP reload. If one frame's work runs past the
  next frame and IVG10 re-enters, it likely overwrites its own frame and crashes. (INFERRED;
  SNEN semantics are from the PRM.) This is post-scan only for our code, so it can be recovered
  with the combo.

### 2.3 Worker 0xffa02324 (CONFIRMED, l1 ffa02324-ffa023a4)
1. W1C DMA3 status (DMA5 0xffc00d68 if [0xff8010b8] != 0, i.e. 192k).
2. CALL 0xffa02634(R0 = rec 0xff802c84, R1 = usbplay 0xff802c5c). This is the USB side: it sends
   the PREVIOUS rec frame, packed <<4 (S), logical >>8, 24-bit (0xffa0270c-0xffa0274e). It is a
   state machine on struct 0xff801100. It also pulses PORTH bit (W[0x447130]-32).
3. SPORT unpack/pack: 0xffa09e88 (48k/96k) or 0xffa0a5f0 (192k). TX = previous out frame
   0xff802c2c scaled `(W[0xff8003ac]*x)>>9`.
4. `P0 = [0xff8010b4]; CALL (P0)` with R0 = in 0xff802bfc, R1 = out 0xff802c2c,
   R2 = usbplay 0xff802c5c, [SP+0xc] = rec 0xff802c84. There is no NULL check.

### 2.4 Callback selection (CONFIRMED)
- ioctl cmd 0 (0x36641a-0x36646a) writes [0xff8010b8] = (rate == 192000), CALL 0x36616c(rate)
  (memsets buffers, builds ping-pong records), then **[0xff8010b4] = fn at 0x366440** (the ONLY
  writer in main and L1), [0xff8010bc] = 8, then SPORT start.
- fn comes from table 0x436214 (file 0x9373c) = [mode*3 + col], with col 2 = 192000,
  1 = 96000, else 0. **44.1k, 48k and 88.2k all use column 0 = 0xffa08054.**
  - Row 0: {0xffa08054, 0xffa089a0, 0xffa092ec}.
  - Rows 1-4: empty stubs 0xffa08048/3c/30/24, and NULL at 192k.
- Rate/mode changes come from task 10 (0x31dff8, pri 4, TA_ACT), handler 0x31dc18. It issues
  ioctl 1 (stop) at 0x31dc78, then ioctl 0, so the pointer is rewritten on every change.

### 2.5 48k callback 0xffa08054 chain order (CONFIRMED, l1 0xffa08054-0xffa088e6)
| # | step | where |
|---|------|-------|
| 0 | in[8],in[9] (digital in) = 0 unless [0xffb003a8] bit4; else CALL 0xffa0000c | ffa0805e-ffa0808c |
| 1 | trim, Q13: `in[i] = (W[0xff800158+2i]*in[i])>>13`, i=0..7, 0x2000 = unity | ffa08090-ffa080ba |
| 2 | meters -> [0xff801650]/[0xff801660] | ffa080bc-ffa0811c |
| 3 | polarity: bit i of [0xff80166c] -> in[i] = -in[i] | ffa08120-ffa0818e |
| 4 | IIR low-cut, coefs from [0xff802910+0x14] | ffa08190.. |
| 5 | COMP 0xffa098ea(in, in, 0xffb00644, [SP+0xc] = 0xffb00624), 4 linked pairs, lookahead, returns with L3 != 0 | CALL at 0xffa0832a (file 0xa08f6) |
| 6 | mix/reverb, two paths on [0xff802a28] bit2; reverb core via [P0+0x1c]; L0-L3 cleared at 0xffa08554 | ffa0839e.. / ffa088e8.. |
| 7 | **record send**: rec[0..9] = in[0..9] (post-trim, polarity, IIR, comp); rec[10..11] = [0xffb0058c]/[0xffb00590] (bus 2/3) | ffa085be (P4 = in), ffa08866-ffa088a2, P3 = 0xffb00584 at ffa0858a |
| 8 | B[0xff800d74] \|= 1 (frame done; consumer: task 15 via 0xffa00584) | ffa088cc-ffa088d8 |
| - | exit with L0-L3 = 0 | ffa088da-ffa088e6 |

### 2.6 Format, block size, channels
- Inside the frames, full scale = +/-2^27 (32-bit words with 4 bits of headroom). Gain Q13,
  out scale >>9. (CONFIRMED by the shifts listed above)
- Block size: **1 sample frame per interrupt**. DMA3 X_COUNT = 4, Y_COUNT = 2 (ping-pong),
  DI on each row. The frame buffers are 10-12 words. (INFERRED: one row = one frame was not
  traced through the SPORT TDM setup.) Plan A's period value settles it.
- Channel layout:
  - in[0..7] = analog ch 1-8 (INFERRED order); in[8..9] = digital (S/PDIF) in (INFERRED).
  - rec[0..9] = those 10 channels; rec[10..11] = an internal stereo bus (INFERRED: mix or
    reverb return).
  - SPORT0 TX words: w0 = out[0]>>1, w2 = out[1]>>1, w1 = out[8], w3 = out[9]
    (0xffa09edc-0xffa09f74, CONFIRMED).
- Per-sample cycle budget (CCLK unknown): 48k = 7760 (CLKIN 19.6 MHz, 1 ms tick) or 9728
  (CLKIN 24.576 MHz); 96k = 3880/4864; 88.2k = 4222/5294. (INFERRED)

## 3. Boot order vs the key scan, and the safety gate

### 3.1 What runs when (CONFIRMED unless noted)
- crt -> meminit 0x30f044 (a bad table hangs at 0x300136 = brick) -> main -> kernel.
- The 'cache' device init 0xffa01880 sets IMEM_CONTROL = 6 and DMEM_CONTROL = 0xe, so SDRAM
  code is I-cached and SDRAM data is D-cached write-through with supervisor write.
- Auto-start tasks include 10 (0x31dff8 audio control, pri 4), 6 (0x31e894 'dif', pri 4) and
  16 (0x31d9b4, pri 7). Task 16 does act_tsk(12) at 0x31da20.
- Task 12 = key scan 0x31bba0 (pri 5, not TA_ACT). Its pre-scan work: 0x300d84, 0x30e5b4,
  0x300be0, 0x34f31c, sta_cyc(3), [0x43f910] = 2, then the scan at 0x31bbf6. After that,
  0x31bd0a-0x31bd22 dispatches through 0x435db0 = {0x31bd74, 0x31bda0, 0x31bda0, 0x31bdc4,
  0x31bd84}.
- Mode 0 = 0x31bd74: CALL 0x30dfdc(R0 = 2) at **0x31bd7a**, then loops CALL 0x30e198 (UI loop).
- UI loop 0x30e198: 50 ms flag -> 0x35a9f4 -> 0x359958 -> **CALL 0x30ddc8 at 0x359a16** (every
  tick). 0x359958 is called only from 0x35a9f4; 0x35a9f4 only from 0x30e2d2; 0x30e198 only from
  0x31bd7e/0x31bd9a/0x31bdbe. No data pointers to any of them.
- **Unresolved:** whether task 10 receives a type-0 message (audio start) before 0x31bbf6. It
  has higher priority than task 12, and the senders sit in parameter tables 0x436294.. and
  0x31eaf0. So the ISR and the L1 callbacks may run pre-scan. (INFERRED: treat as yes)

### 3.2 Gate design (all hook points proven post-scan)
| patch | stock bytes | why it is post-scan only |
|-------|-------------|---------------------------|
| 0x31bd7a (file 0x1bc7a) CALL 0x30dfdc -> CALL arm_stub | ff e3 31 91 | 0x31bd74 is reached only via 0x435db0 from 0x31bd22, mode 0 only. Recovery/updater modes 3/4 never run it. CONFIRMED |
| 0x359a16 (file 0x59916) CALL 0x30ddc8 -> CALL ui_hook | fd e3 d9 a1 | 0x359958 <- 0x35a9f4 <- 0x30e2d2 (UI loop) only. CONFIRMED |
| 0x30de0c (file 0xdd0c) first insn -> `JUMP.S 0x30de00` (fa 2f) | 28 e4 fe ff | dead gate-B overlay; gate [0x4094e8] = 0, no writer. CONFIRMED |
| 0x30de96 (file 0xdd96) first insn -> `JUMP.S 0x30ddfa` (b2 2f) | 2d 82 | dead gate-A overlay; gate [0x4094e4] = 0, no writer. CONFIRMED |
| 0x30de0e-0x30de95, 0x30de98-0x30dfbb | old overlay code | our code + vars; no other branch or pointer into the range. CONFIRMED |

- Second gate: the ARMED word lives in our .text area and is 0 in the file. main is
  LZSS-unpacked fresh to 0x300100 on every boot (notes_safety_bootflow), so it is 0 at every
  power-on. Only arm_stub (post-scan) sets it. ui_hook does nothing but call 0x30ddc8 unless
  ARMED == MAGIC. Even if the reachability analysis were wrong, the new code would be inert.
- The pointer swap happens only from ui_hook. Audio therefore runs pure stock until the first
  50 ms UI tick after the scan. A wrapper bug can only crash post-scan, so it is recoverable
  with the combo (the combo modes never call arm_stub). (CONFIRMED path; the crash behaviour
  is INFERRED)
- No init-table, header, count or size change. meminit sees byte-identical input. The only
  new pre-scan risk is the checksum/packaging, which is the same tool chain that shipped the
  boot screen.

## 4. Free space and how code gets in

| region | size | status | how to load |
|--------|------|--------|-------------|
| .text 0x30de0e-0x30de95 + 0x30de98-0x30dfbb | 136 + 292 = 428 B | CONFIRMED free (dead gated overlay) | overwrite bytes in main.bin in place; same size; recompute checksum |
| L1 code 0xffa048c0-0xffa07fff, 0xffa0a738-0xffa0bfff | ~14 KB + ~6 KB | INFERRED free (no static refs; runtime DMA not excluded) | new init record (pre-scan consumed = brick risk), OR post-scan MDMA/ITEST from arm_stub (INFERRED, preferred) |
| L1 data 0xff8034b4-0xff803fff | ~2.9 KB | INFERRED free (no refs) | no record needed: arm_stub zero-fills it post-scan |
| L1 data bank B 0xff900000.. | 0 | REFUTED: task stacks | - |
| SDRAM after the image (0x3a2d9c-0x3fffff) | ~370 KB | INFERRED (not checked against runtime use or loader limits) | append to main.bin after the init table (table pointer at file 0x6e56c is unchanged); needs a check that nothing uses that range and that the loader/updater accept a bigger image |

- Plans A and B fit in the 428 B .text area. Plan C needs L1 or the post-image SDRAM.
- Branch reach: SDRAM <-> SDRAM CALL/JUMP.L is fine. SDRAM <-> L1 must go through `CALL (Pn)`,
  as the stock code does (e.g. 0x32bb3a-0x32bb42). (CONFIRMED pattern)
- Do not execute code that was modified at runtime in SDRAM (the I-cache does not snoop).
  Keep variables on their own 32-byte line after the code. (INFERRED)

## 5. Plan A: CPU-load meter

### 5.1 Measurement (A1 = DSP load, ships first)
- arm_stub, post-scan: `R1 = SYSCFG; BITSET(R1, 1); SYSCFG = R1; CSYNC;` (CCEN; keep bit 2
  SNEN). Tasks run in IVG15 supervisor, so this is legal. (CONFIRMED supervisor; bit meaning
  from the PRM)
- The wrapper reads CYCLES at entry (t0) and after the stock callback returns (t1):
  - `period = t0 - last_t0; last_t0 = t0` (full frame time in core cycles)
  - `busy = t1 - t0` (stock chain + our code)
  - `busy_max = max(busy_max, busy)`; `period_last = period`
- ui_hook takes a snapshot every 50 ms under CLI, resets busy_max, and draws.
- Display: raw `"D%4d/%4d"` = busy_max/period, e.g. `D1830/9728`. This needs no divide (there
  is no 32-bit divide helper in .text, CONFIRMED). It gives the real cycles per sample, and
  period*48000 = CCLK, which settles CLKIN (19.6 vs 24.576 MHz). A percentage can come later
  with a 16-step shift-subtract.
- What it does not see: ISR save/restore, 0xffa02634 (USB), unpack/pack. busy can include
  nested IVG7-9 time; the max shows that as spikes. (INFERRED)
- A2 (later, total CPU): replace IVG14 (EVT14 0xffe02038) post-scan with a copy of the idle
  handler that timestamps around IDLE, using TIMER2_COUNTER 0xffc00624 as the timebase. Copy
  the stock tail 0xffa014ee-0xffa01518; do not JUMP back into L1. (INFERRED design, see the
  timing-lcd map)

### 5.2 Code sketch (all in the 0x30de0e+ area)
```
arm_stub:                       ; from 0x31bd7a, R0 = 2 must survive
    R1 = SYSCFG; BITSET(R1,1); SYSCFG = R1; CSYNC;
    P0.L = vars; P0.H = vars;    ; zero busy_max/last_t0/period
    R1 = 0; [P0+BMAX] = R1; [P0+LAST] = R1; [P0+PER] = R1;
    R1.L = MAGIC_lo; R1.H = MAGIC_hi; [P0+ARMED] = R1;
    JUMP.L 0x30dfdc;             ; tail call, RETS -> 0x31bd7e

wrap48:                         ; installed into [0xff8010b4]; R0/R1/R2 = in/out/usbplay
    [--SP] = (R7:6, P5:5); [--SP] = RETS;
    R7 = CYCLES;                 ; t0
    P5.L = vars; P5.H = vars;
    R6 = [P5+LAST]; [P5+LAST] = R7; R6 = R7 - R6; [P5+PER] = R6;
    SP += -16; R3.L = 0x2c84; R3.H = 0xff80; [SP+0xc] = R3;   ; 12 B arg area + rec arg
    P0.L = 0x8054; P0.H = 0xffa0; CALL (P0);                  ; stock 48k chain
    SP += 16;
    ; --- Plan B goes here ---
    R0 = CYCLES; R0 = R0 - R7; R1 = [P5+BMAX]; R1 = MAX(R0,R1); [P5+BMAX] = R1;
    RETS = [SP++]; (R7:6, P5:5) = [SP++]; RTS;                ; ~24 B stack

ui_hook:                        ; from 0x359a16
    LINK 0x40; [--SP] = (R7:6, P5:5);
    P5 = vars; R0 = [P5+ARMED]; CC = R0 == MAGIC; IF !CC JUMP plain;
    CLI R2;                                     ; (re)arm: task 10 may rewrite the pointer
      P0 = 0xff8010b4; R0 = [P0]; R1 = 0xffa08054; CC = R0 == R1;
      IF CC R0 = wrap48 (via R1 load + conditional move); [P0] = R0;
      R6 = [P5+BMAX]; R7 = [P5+PER]; R0 = 0; [P5+BMAX] = R0;
    STI R2;
    CALL 0x30ddc8;                               ; stock first (callback list draws)
    ; draw, same calls as the stock overlay template 0x30de0c-0x30de94:
    R0 = [0x440944]; CALL 0x34df50;              ; save state (INFERRED role)
    sprintf 0x324010(R0 = buf on stack, R1 = fmt, R2 = busy_max, [SP+0xc] = period);
    SetFont 0x34dfdc(R0 = g, R1 = 11);           ; 4x6 font (font table 0x418bec)
    0x34dee2(g, 0x01000000, 0x01000002) x2;      ; colours, as the template (INFERRED role)
    DrawString 0x34edd8(R0 = g, R1 = x, R2 = y, [SP+0xc] = buf);
    R0 = g; CALL 0x34df02;                       ; restore
    JUMP done;
plain: CALL 0x30ddc8;
done:  (R7:6, P5:5) = [SP++]; UNLINK; RTS;
```
- Notes:
  - Only the exact stock pointer 0xffa08054 is swapped, and the wrapper calls the constant
    0xffa08054. There is no "orig" variable, so a stale orig cannot pair with a new rate.
  - CLI covers the read-compare-write against task 10 (pri 4 can pre-empt the UI task 12, pri 5).
  - After a rate change, task 10 writes the stock pointer back and the next tick (<= 50 ms)
    re-arms.
  - 96k/192k stay stock until wrap96/wrap192 exist.
- ABI (CONFIRMED from the worker/callback frames): preserve R4-R7, P3-P5, FP; leave L0-L3 = 0
  (the stock callback already returns with them 0); no TOPPERS calls in the wrapper (IVG10 is
  non-kernel).
- Placement on screen: unknown. It needs a phone photo of the home screen. The stock overlay
  rects at 0x41d5a4 are off-screen ({4,192,...}). Whether the text reaches the glass on every
  screen, and whether 0x35931a (the second caller of 0x30ddc8) repaints over it, needs one
  hardware look. (INFERRED)

## 6. Plan B: one-channel audible test

- **Recommended (B1): record-only -6 dB on ch1.** Put this in wrap48 after the stock callback
  returns:
  ```
  P0.L = 0x2c84; P0.H = 0xff80; R0 = [P0]; R0 >>>= 1; [P0] = R0;   ; rec[0] *= 0.5
  ```
  - rec[0] is the frame 0xffa02634 sends to USB on the next ISR (CONFIRMED: written at
    0xffa08866.., consumed at 0xffa0270c..).
  - The change happens after trim/polarity/IIR/comp, so it is an exact -6.02 dB on one recorded
    channel. The monitor mix and the comp are unchanged.
  - Cost: about 6 cycles. Stack: none extra.
- Make it unmistakable (optional B1b): a chopper, `if (frame_count & 0x8000) rec[0] = 0`. At 48k
  that mutes ch1 for ~0.68 s on, ~0.68 s off in the recording.
- B2 (pre-chain, affects monitor + comp): `R3 = [R0]; R3 = -R3; [R0] = R3` (polarity) or
  `>>>= 1` on in[0] before CALLing 0xffa08054. This proves the wrapper can feed the stock comp.
  Do it after B1.
- B0 (no code, mapping only): flip ch1 polarity with the stock UI (mask [0xff80166c] bit0 is
  stock) and confirm which USB channel inverts. This only maps slot 0 to a USB channel; it does
  not prove our code runs.
- Test: 48 kHz (or 44.1/88.2k, which also use 0xffa08054), a steady source into input 1 and
  input 2 at the same level. Record 2 channels in the DAW. Expect ch1 6 dB below ch2 once the UI
  is up (rec ch1 = in[0] is INFERRED). If nothing changes, check the meter: if busy/period shows,
  the wrapper runs and the slot mapping is wrong.

## 7. Plan C (later): per-channel biquad EQ next to the comp

- **Placement without L1 patching:** in the wrapper, process in[0..7] BEFORE calling the stock
  callback. The order becomes EQ -> trim -> polarity -> IIR low-cut -> COMP -> mix/record, i.e.
  "EQ before comp", which is the normal channel-strip order, and it feeds both monitor and
  record.
- A true post-comp EQ (at CALL 0xffa0832a, file 0xa08f6) is an L1 init-table change in code that
  may run pre-scan, so the brick rule forbids it until the audio-start-vs-scan question is
  settled. Other hazards there: the comp returns with L3 != 0, and the trampoline must keep
  P3/P4/P5/R7/FP.
- Arithmetic: the frames are 32-bit Q27. Use a DF1 biquad with 32-bit state and 32x16 MACs (two
  MACs per tap, as the stock trim does: A0 = g*x.H, A1 = g*x.L>>>16). Use a double-precision
  feedback path (32x32 or error feedback) for the HPF/low shelf, because 16-bit coefficients fail
  with poles near 1.
- Cycle estimate per biquad per channel per sample (INFERRED):
  - L1 code, L1 data state: ~25-35 cycles (16-bit coefs); ~40-50 (32-bit feedback).
  - SDRAM code (I-cache): add ~10-30 % plus miss jitter.

| config | cycles/sample | 48k (7760-9728) | 96k (3880-4864) |
|--------|---------------|-----------------|-----------------|
| 1 biquad x 8 ch | 200-400 | 2-5 % | 4-10 % |
| HPF + 3-band (4 bq) x 8 ch | 800-1600 | 8-21 % | 16-41 % |
| + 2 digital ch (10 ch) | x1.25 | | |

- The stock chain cost is unknown until Plan A runs. Keep the total below ~70 % of the frame
  because of the SNEN overrun hazard. Skip the EQ when period < busy margin.
- Code home: SDRAM first (simplest). For speed, use free L1 0xffa048c0.., loaded post-scan from
  arm_stub by MDMA (stock meminit uses MDMA0 0xffc00f00.. via bounce buffer 0x440de0, CONFIRMED)
  or ITEST. Verify MDMA0 is idle at runtime first. Coefficient and state RAM: 0xff8034c0..,
  zeroed by arm_stub.
- Rates: wrap96 (calls 0xffa089a0) and wrap192 (0xffa092ec) are separate wrappers with
  stride-correct in[] handling. 192k has 2432-3104 cycles/sample, so EQ there is probably
  1 band max.
- UI: parameters as plain words in L1 data, written by a menu task (Rosetta-style scrolling
  menus). Write coefficients with double-buffer + pointer swap so the ISR never sees a
  half-written set.

## 8. Open questions (ranked)

1. **Does the text drawn by ui_hook reach the glass, and where?** Settle with HW test 1 (the
   Plan A build) plus a phone photo of the home screen for x/y. If not, fall back to drawing
   at 0x30e674 (inside present 0x30e650), ARMED-gated.
2. **Is rec[0] / in[0] = input 1 -> USB ch1?** Settle with the Plan B recording (one take).
   Offline partial check: B0 with the stock polarity UI.
3. **CLKIN / CCLK and interrupt rate (1 frame per IRQ?)** Settled by Plan A's period readout:
   period*Fs = CCLK; it should be ~7760 or ~9728 at 48k. A value 2x too small means 2 IRQs per
   frame.
4. **Does audio start before 0x31bbf6?** This only matters for L1/table patches (post-comp EQ).
   Settle offline by following the indirect calls from tables 0x436294-0x436360 and 0x31eaf0
   (who walks them at boot, and in which task). Or with HW: arm_stub records [0xff8010b4] != 0
   and B[0xff800d74] at arm time and shows them on the LCD.
5. **Stock chain cycles at 48k/96k** (decides Plan C size). Settled by Plan A.
6. **SNEN self-nest on overrun:** confirm in the BF52x PRM (offline). Design around it anyway
   (budget + skip).
7. **IVG10 stack headroom:** the frames of the comp internals and of the reverb core
   [0xff802a44] callees. Offline: disassemble and sum. Our wrapper uses ~24 B, so this is not
   blocking for A/B.
8. **Free L1 I/D and the post-image SDRAM really unused at runtime; MDMA0 idle after boot.**
   Offline grep for MDMA0 users and DMA dests; HW: fill with a pattern from arm_stub, check
   later.
9. **PH3 scope probe:** is PH3 an output on a pad, and is the USB pulse bit (W[0x447130]-32)
   also PH3? Optional; Plan A replaces it.
10. **Ramp/parameter engine rewriting trim W[0xff800158]:** only matters if we use the stock
    trim as a test (we do not).
