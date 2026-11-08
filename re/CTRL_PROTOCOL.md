# OCTA-CAPTURE CTRL protocol (Control Panel <-> unit)

This is the wire protocol a new PC app needs to replace Roland's "OCTA-CAPTURE Control Panel".
It covers preamps, the direct mixer A-D, reverb, patchbay, system actions and meters.

Sources (all read-only):
- Firmware: stock v1.51, `re\main.dis` plus the runtime image. The RAM init table and the registry constructor were run in a scratch Blackfin emulator.
- PC side: `RDDP1110.EXE` (the panel), `RDWM1110.SYS` (WDM driver), `RDAS1110.DLL` (ASIO).

Two independent readers did the work, and a verifier cross-checked them. No hardware, MIDI port or capture was used.

Every claim is marked:
- **C** = confirmed from code that was read, with addresses. "fw" means a firmware address (0x3xxxxx / 0x4xxxxx); "drv" and "panel" mean addresses in the Windows binaries (0x14xxxxxxx).
- **I** = inferred.

---

## 1. Summary and transport

### What the protocol is

| Item | Fact | C/I | Evidence |
|---|---|---|---|
| Protocol | Plain Roland SysEx: `F0 41 10 00 00 4D` + RQ1 (0x11) / DT1 (0x12). There is no private framing. | C | fw templates 0x436384/0x43638c; drv 0x1400067de |
| Who sends it on the PC | The panel does **not** use MIDI; its only WINMM import is PlaySoundW. It opens `\\.\Rdwm1110000` and calls DeviceIoControl 0x22225c with a 0x28-byte {class, a, b, c, d} request. The driver turns each request into SysEx. | C | panel strings 0x208200/0x208218, wrappers 0x140004520, 0x140016050 |
| Driver send cable | The driver sends on USB-MIDI cable 2: send function 0x14001b5e0 is called with edx=2 and does `shl dl,4`, so the header byte is 0x2x. | C (that this byte is the cable field: I) | drv 0x14000ab43 -> 0x14001b5e0 |
| Unit receive | The Roland SysEx listener is registered with type 0x10 (SysEx), cable mask 7 (cables 0, 1, 2) and callback 0x31f250. | C | fw 0x31f574, registration fn 0x3693ac, table 0x419fec+0x1020 |
| Cable 0 | Cable 0 is the DIN MIDI port and never reaches the SysEx task, according to the lab notes. | I | docs\LAB.md |
| Unit send | Every outgoing message is sent twice through 0x36965c, on stream 0 and stream 1. Those streams were registered as 0x3695fc(0,1) and 0x3695fc(1,2). | C | fw 0x31fabe/0x31fac8, 0x31f58c/0x31f596 |
| What that means for cables | Stream 0 = cable 1 (the "CTRL" port the lab uses) and stream 1 = cable 2 (the driver's cable). So each reply arrives on **both** cables. | I | |
| Checksum | The unit never checks the checksum on receive. | C | fw assembler 0x31f47a-0x31f4c4 drops cs and F7; parser 0x31fc2c has no check |
| Device ID | 0x10 only. Broadcast 0x7F is never accepted. | C | exact template compare fw 0x31f274-0x31f2c0 |

### Lab hook

- Our hook sits in task 9's dispatcher at 0x369468. It **eats only `F0 7D 4F ...`**; every Roland SysEx passes through to 0x31f250 untouched (docs\LAB.md, 17 host tests).
- So a Roland-protocol app works on both stock and lab firmware.
- The hook does **not** log Roland traffic. Section 6 covers how to capture it.

### Practical port choice for a new app (I)

- Open the Windows MIDI port for the CTRL cable (cable 1), the same port `octa_lab.py` uses.
- Close the Roland Control Panel first: Windows MIDI ports usually allow one program at a time.
- The Roland driver keeps parsing the unit's DT1s into its own mirror whatever else is open. That is harmless.

---

## 2. Framing

### Message layout

`F0 41 10 00 00 4D cmd a0 a1 a2 a3 data... cs F7`

| Field | Value | C/I | Evidence |
|---|---|---|---|
| Manufacturer | 0x41 (Roland) | C | fw 0x436384 |
| Device | 0x10 (fixed) | C | fw 0x31f274 |
| Model | `00 00 4D` (3 bytes) | C | fw 0x436384, drv 0x1400067ef |
| cmd | 0x11 RQ1, 0x12 DT1; anything else is dropped (assembler state 4) | C | fw 0x31f456-0x31f46a |
| a0 | 0x00 = single parameter. 0x01 = bulk: RQ1 = dump request, DT1 = write the whole state; a1-a3 are ignored. Only `a0==1` is tested. The unit always sends a0 = 00. | C | fw 0x31fc5e, 0x31fe3c |
| a1 | Block 0x00-0x0A | C | registry patterns |
| a2/a3 | Sub-address. In a wildcard position: byte = (hi<<4) \| lo. Receive: hi = byte>>4, lo = byte&0xF. Send: ((hi&7)<<4) \| (lo&0xF). | C | fw rx 0x31fd98-0x31fda2, tx 0x31faf4-0x31fb14 |
| data | At least 1 byte, or the parser exits. Up to 0x7FC bytes are buffered (0x441208). | C | fw 0x31fc34, 0x31f3f8 |
| cs | (-(sum of a0..a3 and data)) & 0x7F. Not checked on receive, but always send a correct one. | C | fw tx 0x31fa62-0x31faa4; drv 0x14000a710 |

### Data encodings by registry value type

Receive table: fw 0x436408. Send table: fw 0x4363e4.

| Type | Wire data | Value | C/I | Evidence |
|---|---|---|---|---|
| 1, 2, 3, 8 | 1 byte | 7-bit | C | fw 0x31fe60 / 0x31fb74 |
| 4 | 4 bytes, one nibble each, MSB first | 16-bit | C | fw 0x31fe90 / 0x31fba8; drv 0x140006fdc |
| 5 | 6 bytes, one nibble each, MSB first | 24-bit | C | fw 0x31fec0 / 0x31fbd4; drv 0x140009cb0 |
| 7 | 2 bytes, **low nibble first**, then high nibble | 8-bit | C (both sides) | fw 0x31ff06 / 0x31fc14; drv 0x1400068d6 |
| 0 | 1 byte; the unit never sends it (write-only) | - | C | send table: R2 = 0, fw 0x31fb7e |
| 6 | 11 raw bytes (quick strip); never sent | - | C | same |

### Worked examples (checksums computed by hand)

**SENS ch1 = 30.0 dB.** Raw value = 60 = 0x3C. Address `00 05 00 04`.
- Sum = 0x00+0x05+0x00+0x04+0x3C = 0x45 = 69. cs = (128 - 69) & 0x7F = **0x3B**.
- MIDI: `F0 41 10 00 00 4D 12 00 05 00 04 3C 3B F7` (14 bytes).
- USB-MIDI packets on cable 1 (CIN 4 = SysEx start/continue, CIN 6 = end with 2 bytes):
  `14 F0 41 10 | 14 00 00 4D | 14 12 00 05 | 14 00 04 3C | 16 3B F7 00`

**Bulk dump request** (exactly what the driver sends, drv 0x14000aa20):
- `F0 41 10 00 00 4D 11 01 00 00 00 00 00 08 7A 7D F7`
- The size is `00 00 08 7A` = 1146 in 7-bit. cs: 1+8+0x7A = 131, 131 & 0x7F = 3, so cs = 0x7D.

**Meters on:** `F0 41 10 00 00 4D 12 00 0A 00 00 01 75 F7`. **Meters off:** `... 00 0A 00 00 00 76 F7`.

### Identity (C)

- Request: `F0 7E 10 06 01 F7` (template fw 0x43638c). `F0 7E 7F 06 01 F7` gets **no** reply.
- Reply: `F0 7E 10 06 02 41 4D 02 00 00 00 00 00 00 F7` (fw 0x436394; family 0x024D, member 0000). It is sent on both streams.

### Remote key injector (not used by the panel)

- `F0 41 78 a b c d F7`: packet 2 must be CIN 4 and packet 3 must be CIN 6. Checked by fw 0x35b3b6 (C).
- It calls 0x35ab58(a,b,c,d), which queues UI event 0x26 with payload {b,c,d} (C).
- It then routes to onReceive 0x30e128 -> 0x30e024 (command table 0x41d600: 1 = release, 2 = press, 4 = encoder) (I).
- This closes the UI_MAP open item "remote injector messages" (I, needs a test).

---

## 3. Address map

How to read the tables:
- `c` = preamp channel 0-7. `m` = direct mix 0-3 (A-D). `i` = mixer strip 0-9. `t` = reverb type 0-4. `p` = output pair 0-4.
- Dir: `both` = the PC writes it and the unit sends it on change and in the bulk dump. `W` = write-only (never sent by the unit). `R` = unit-sent only (writes are ignored).
- Every firmware set handler ends in fw 0x301a88(obj, id, value, ch), unless noted.

### 3.1 Preamp / compressor, per channel (block 05, preamp object 0x43f444, ch = c)

| Address | Param | fw id | Type / size | Range, units | Panel class | C/I | Evidence |
|---|---|---|---|---|---|---|---|
| `00 05 0c 00` | +48V | 11 | t1, 1 B | 0/1 | CBtnPrePhantom 0x1c | C | id getter fw 0x31f94c; set 0x32113c; drv 0x1400069fb |
| `00 05 0c 01` | Lo-Cut | 8 | t1 | 0/1 | CBtnPreLoCut 0x1f | C | fw 0x31f93c; drv 0x140006b06 |
| `00 05 0c 02` | Phase invert | 9 | t1 | 0/1 | CBtnPrePhase 0x1d | C | fw 0x31f92c; drv 0x140006aad |
| `00 05 0c 03` | Hi-Z | 12 | t1 | 0/1. Which channels honour it (1-2?) is I. | CBtnPreHiZ 0x1e | C | fw 0x31f91c; drv 0x140006a54 |
| `00 05 0c 04` | SENS (gain) | 0 | t3 | 0-100 = 0.0-50.0 dB in 0.5 dB steps | CBtnPreSens 0x20 | C | fw 0x31f908; drv 0x140006b5f; panel limit < 0x65 at 0x14002365b |
| `00 05 0c 05` | Stereo LINK | 1 | t2 | 0/1. The panel writes c = 2*pair (even channel); the firmware accepts any c. | CBtnPreLink 0x14 | C | fw 0x31f8f8; drv 0x140006bae (`add dl,dl`) |
| `00 05 0c 06` | COMP on | 10 | t1 | 0/1. The Bypass button also drives it. | CBtnPreComp 0x15 | C | fw 0x31f8e8; drv 0x140006bed |
| `00 05 0c 07` | GATE | 7 | t3 | 0 = off; dB = v - 70 (to -INF..) | CBarCompGate 0x16 | C | fw 0x31f8d8; panel fmt 0x1400233c0 |
| `00 05 0c 08` | ATTACK | 5 | t3 | 0-25 = 0.2, 0.5, 1, 2, 4, 6, 8, 10, 15..100 ms | CBarCompAtk 0x17 | C (ms: I) | fw 0x31f8c8; panel fmt 0x140023340 |
| `00 05 0c 09` | RELEASE | 6 | t3 | 0-45 = 10..500 ms | CBarCompRel 0x18 | C | fw 0x31f8b8; panel fmt 0x140023440 |
| `00 05 0c 0A` | THRESHOLD | 2 | t3 | dB = v - 50; maximum unchecked | CBarCompThres 0x19 | C | fw 0x31f8a8; panel fmt 0x140023380 |
| `00 05 0c 0B` | RATIO | 3 | t3 | 0-8 = 1.0, 1.2, 1.5, 2.0, 2.8, 4.0, 8.0, 16, INF | CBarCompRatio 0x1a | C | fw 0x31f898; panel fmt 0x140023400 |
| `00 05 0c 0C` | makeup GAIN | 4 | t3 | dB = v - 50; maximum unchecked | CBarCompGain 0x1b | C | fw 0x31f888; panel fmt 0x140023380 |

### 3.2 Direct mixer (mixer object 0x43f74c, ch = m)

Block 06 = input strips (fw ids 0-9 base). Block 07 = DAW/WAVE playback strips (ids 10-19 base). That 06 = inputs and 07 = DAW is I.
That strips 0-7 = analog in and 8-9 = S/PDIF L/R is I.

| Address | Param | fw id (06 / 07) | Type / size | Range | Panel class | C/I | Evidence |
|---|---|---|---|---|---|---|---|
| `00 06/07 mi 00` | Strip LINK (the panel writes i = 2*pair) | 0+i / 10+i | t2, 1 B | 0/1 | CBtnLink 0x21 | C | getter 0x31f854; set 0x32169c; drv 0x140006e20 |
| `00 06/07 mi 01` | MONO | 63+i / 73+i | t2 | 0/1 | none found (class 0x22) | C | 0x31f844; drv 0x140006e78 |
| `00 06/07 mi 02` | SOLO | 103+i / 113+i | t1 | 0/1 | CBtnSolo 0x24 | C | 0x31f834; drv 0x140006f25 |
| `00 06/07 mi 03` | MUTE | 123+i / 133+i | t1 | 0/1 | CBtnMute 0x23 | C | 0x31f824; drv 0x140006ed0 |
| `00 06/07 mi 04` | PAN | 43+i / 53+i | t4, 4 nibbles | 16-bit. The panel reset value is 0x4000 (centre); endpoints 0..0x8000 are I. | CBarDmixPan 0x26 | C (scale I) | 0x31f814; drv 0x140006fb5; panel 0x140016385 |
| `00 06/07 mi 08` | Fader "VR" | 23+i / 33+i | t5, 6 nibbles | 24-bit. The panel reset value is 0x200000 (0 dB?); the dB curve is unknown. | CBtnVol 0x27 | C (scale I) | 0x31f804/0x31f758; drv 0x14000707e; panel 0x140016193 |
| `00 06 0i 0E` | Reverb SEND (one per input; there is no per-mix send) | 83+i, ch 0 | t5 | 24-bit, 0 = off | CBarDmixSend 0x25 | C | 0x31f7f4; drv 0x140006f7a; registry dims [1,10] |
| `00 06 ii 14` | Quick strip, MIX A only. ii = 0-9, or 7F = all 10. Data: [mute][pan: 4 nibbles][VR: 6 nibbles]. Sets MUTE, PAN and VR in one message. | 123+i, 43+i, 23+i | t6, 11 B, **W** | as above | ASIO Direct Monitor only (IOCTL 0x222144) | C | fw 0x3219f0; drv 0x140006300/0x140009b70 |
| `00 08 m0 00` | Master link ("LINK IN") | 21 | t1 | 0/1 | CBtnMstLink 0x2a (a=2) | C | fw 0x321df0; drv 0x140007133 |
| `00 08 ms 01` | Master level, s = 0/1 (L/R is I) | 143+s | t5 | 24-bit; panel reset 0x200000 | CBtnMstVol 0x2c (a=2) | C | fw 0x321e7c; drv 0x1400071ae |
| `00 08 00 07` | Reverb RETURN (global; a2 is a wildcard, but there is one value) | 147, ch 0 | t5 | 24-bit | CBarDmixRet 0x2b | C | fw 0x321f24; drv 0x140007178 |
| `00 08 m0 0D` | "LINK M" / master IO link | 20 | t1 | 0/1 | CBtnMstIOLink 0x28 | C (function I) | fw 0x321fcc; drv 0x1400070ad |
| `00 08 m0 0E` | Placeholder: writes are ignored, reads 0 | - | t1, **R** | 0 | none (class 0x29) | C | fw set 0x31f6fc is empty |
| `00 09 m0 00` | "LINK OUT" | 22 | t1 | 0/1 | not seen in the panel | C | fw 0x3220a8 |
| `00 09 ms 01` | Second master level pair | 145+s | t5 | 24-bit | not seen (would be class 0x2c with a=3) | C (meaning I) | fw 0x322134 |

Mixer ids 93-102 (the DAW reverb sends) exist in the firmware but have **no SysEx address** (C, from the registry).

### 3.3 Reverb (block 04, reverb object 0x43f7a8)

| Address | Param | fw id, ch | Type | Range | C/I | Evidence |
|---|---|---|---|---|---|---|
| `00 04 00 00` | Reverb type | 1, 0 | t3 | 0-4 = OFF, ECHO, ROOM, S.HALL, L.HALL (order is I) | C | fw 0x320f98; drv 0x14000698f |
| `00 04 0t 01` | PRE DELAY of type t | 2, t | t3 | unscaled; units ms (I) | C | fw 0x321024; drv 0x1400069a7, CSpRevPreDly |
| `00 04 0t 02` | TIME of type t | 3, t | t3 | unscaled; units s (I) | C | fw 0x3210b0; drv 0x1400069d1, CSpRevTime |

### 3.4 Patchbay (block 03, object 0x43f780)

| Address | Param | Value | C/I | Evidence |
|---|---|---|---|---|
| `00 03 00 0p` | Source for output pair p (OUT 1-2 .. 9-10) | The wire value is 0-8; internally it is stored as value+1, so internal 0 (MUTE?) cannot be set. Wire 0-3 = DIRECT MIX A-D and 4-8 = WAVE OUT 1-2..9-10 (I, from the dialog list). | C (offset), I (names) | fw set 0x320f00, get 0x320f28; drv 0x140006949 |
| (panel macro) | MULTIPLE-MONITOR on: writes pairs 0-4 = 0, 1, 2, 3, 0 | | C | panel 0x140016ed3-0x140016f2a |

### 3.5 System, AUTO-SENS, status (blocks 00-02)

| Address | Meaning | Dir | Type | C/I | Evidence |
|---|---|---|---|---|---|
| `00 00 00 00` | The panel sends 0 (device button CBtnDev). The firmware loads a 200 ms countdown that drives 'lcd' ioctl 4. Probably "panel connected / identify". | W | t0 | C (code), I (meaning) | fw set 0x320b74 -> 0x30e504, task 0x30e554; drv class 6 at 0x1400067d1 |
| `00 00 00 01` | 1 = **restore** settings from unit memory, 0 = **backup** current settings to unit memory. The firmware posts code 5 (non-zero) or 6 (zero) through 0x31f18c. | W | t0 | C (code + driver), I (strings mapping) | fw 0x320bd4; drv 0x14000aad0; panel 0x14001cf80 / 0x14001edd0 |
| `00 00 00 02` | Initialize a section: 0 = all/current, 1 = preamp, 2 = direct mixer, 3 = reverb, 4 = patchbay | W | t0 | C | fw 0x320c50; drv 0x140006861; panel 0x140027fb0 |
| `00 01 00 00` | SYSTEM param 16 of 0x43f8c0, as 0/1 (unnamed) | R | t3 | C | fw get 0x320cb8 |
| `00 02 00 00` | SYSTEM param 14 (unnamed) | R | t3 | C | fw 0x320d58 |
| `00 02 00 01` | Clock id 0, likely the sample-rate index (3 = 192k?) | R | t3 | C (code), I (meaning) | fw 0x320dc4. The driver can write it (class 0xa), but no caller was found. |
| `00 02 00 02` | Clock id 1, reported as 0 or 3 (lock/source?) | R | t3 | C / I | fw 0x320e3c; driver class 0xb has no caller |
| `00 02 00 03` | Constant 1 (capability flag?). The panel's "selected mix" goes into this byte of the bulk write and is ignored. | R | t3 | C | fw 0x31fa04 |
| `00 02 01 04` | AUTO-SENS start with a channel bit mask. The firmware sets AUTO-SENS flags 5+c and posts event **0x2713**. At high sample rates the mask is limited to 0x0F. The unit sends the mask when A-S starts on the front panel. | both | t7 (2 B, lo nibble first) | C (bit 0 = ch1 is I) | fw set 0x32227c; drv 0x1400068be; panel CDlgASens |
| `00 02 01 05` | AUTO-SENS end. 0 posts UI event 6, non-zero posts event 7 (I: cancel/finish; the panel sends 1 or 0). The unit sends 0 when A-S ends. | both | t8 | C (code), I (meaning) | fw 0x322350; drv 0x140006933; panel 0x14001f720 |

This closes the UI_MAP open item: the Control Panel's 0x2713 comes from DT1 `00 02 01 04`.

### 3.6 Meters (block 0A)

| Address | Meaning | Dir | C/I | Evidence |
|---|---|---|---|---|
| `00 0A 00 00` | Meter stream on (non-zero) / off (0). It is stored in [0x4193ec]. | W | C | fw set 0x31f678; drv class 0xf; panel 0x140022ed0 / 0x140022fa0 |
| `00 0A 00 01` | 16 data bytes = 8 x 14-bit (hi7, lo7) peak values, analog inputs 1-8. Linear, 0x3FFF = full scale. Peak hold since the last send, then cleared. | R | C | fw sender 0x3209f4-0x320ace, addr 0x4363e0; source 0x30274e: 0x43ed10[i] >> 13, clamped |
| PC display | dB = 20*log10(v/16383); v = 0 is shown as -96 dB | - | C | panel 0x14000f674 |

**Not sent over SysEx (C):** the S/PDIF inputs, outputs, DAW stream meters and comp gain reduction (0x43ed38/60/88).
The Roland driver computes the PC stream meters from the audio itself (classes 0x2d/0x2e), so a new app must compute its own.

### 3.7 Bulk (a0 = 01)

| Message | Behaviour | C/I | Evidence |
|---|---|---|---|
| RQ1 `01 xx xx xx` + any size | Sets the dump flag [0x441a14]=1. On the next periodic pass the unit sends **one DT1 to `01 00 00 00` with 1146 data bytes** (total message 1159 B). RQ1 with a0 != 1 is silently ignored, so **single parameters cannot be read**. | C | fw 0x31fe3c, sender 0x320574-0x320784 |
| DT1 `01 xx xx xx` + 1146 B | Bulk write: every registry entry is set in order through its own set handler. The panel uses it to load `.octa` files. | C | fw 0x31fde2; drv class 5 at 0x1400066ec |

**Bulk layout** (offsets in the data part). Computed from the registry dump; the total 1146 equals the driver's size field and its 0x487-byte message, which is an independent match (C).

| Offset | Len | Contents | Packing |
|---|---|---|---|
| 0 | 1 | `00 01 00 00` SYSTEM 16 | byte |
| 1-4 | 4 | `00 02 00 00..03` (byte 4 is ignored on write) | byte each |
| 5-9 | 5 | patchbay p0-4 | byte |
| 10 | 1 | reverb type | byte |
| 11-15 | 5 | pre-delay, types 0-4 | byte |
| 16-20 | 5 | time, types 0-4 | byte |
| 21/23/25/27 | 2 each | 48V / Lo-Cut / Phase / Hi-Z, ch 0-7 | t1: 4 bools per byte in bits 0-3 |
| 29-36 | 8 | SENS, ch 0-7 | byte |
| 37 | 1 | preamp LINK, 4 pairs | t2: 1 bit per pair |
| 38-39 | 2 | COMP on | t1 |
| 40/48/56/64/72/80 | 8 each | GATE / ATTACK / RELEASE / THRES / RATIO / GAIN | byte |
| 88, 96 | 8 each | blk06 LINK, MONO: mix 0-3 x 2 B | t2: 5 pair bits per mix in 2 bytes |
| 104, 116 | 12 each | blk06 SOLO, MUTE: mix 0-3 x 3 B | t1 |
| 128 | 160 | blk06 PAN, 4 x 10 x 4 nibbles | t4 |
| 288 | 240 | blk06 VR, 4 x 10 x 6 nibbles | t5 |
| 528 | 60 | input SEND, 10 x 6 nibbles | t5 |
| 588-1027 | 440 | blk07: same layout as 88-527 without SEND (8+8+12+12+160+240) | |
| 1028 | 4 | 08 LINK IN, per mix | t1 (1 byte per mix) |
| 1032 | 48 | 08 master level, 4 mix x 2 x 6 nibbles | t5 |
| 1080 | 6 | RETURN | t5 |
| 1086, 1090 | 4 each | 08 0D LINK M, 08 0E placeholder | t1 |
| 1094 | 4 | 09 LINK OUT | t1 |
| 1098 | 48 | 09 master level | t5 |

Packing confidence:
- Order hi-major, then lo; bit order within the bytes = channel 0 at bit 0. Both are I.
- Type-5 bulk = 6 nibbles: I from the firmware alone (decoder 0x320186 was only partly read), but the 1146 total match makes it very likely.
- Field order in the header matches the driver's builder (drv 0x1400074e0 fills d[5..20]) (C).

---

## 4. Firmware parameter model

All remote writes go through fw **0x301a88(obj, id, value, ch)**.
Reads go through per-type getters (preamp 0x3097e4, reverb 0x30b8c8, patchbay 0x30a92c, system 0x30cf94, clock 0x30beba).
Whether 0x301a88 clamps out-of-range values is **unchecked**, so the app must clamp itself.

| Object | Group | ids (C) | ch | Notes |
|---|---|---|---|---|
| 0x43f444 | Preamp | 0 SENS, 1 LINK, 2 THRES, 3 RATIO, 4 GAIN, 5 ATK, 6 REL, 7 GATE, 8 LoCut, 9 Phase, 10 COMP, 11 48V, 12 Hi-Z (13 has no SysEx) | 0-7 | ids < 14, ch < 9 (brief). Ranges sit behind per-channel sub-objects (getRange 0x30987a -> 0x41d024), so they cannot be read statically. |
| 0x43f74c | Direct mixer | 0-9 / 10-19 LINK; 20 LINK M; 21 LINK IN; 22 LINK OUT; 23-32 / 33-42 VR; 43-52 / 53-62 PAN; 63-72 / 73-82 MONO; 83-92 / 93-102 SEND; 103-112 / 113-122 SOLO; 123-132 / 133-142 MUTE; 143-146 M VR; 147 RETURN | mix 0-3 | category table 0x41cb38 |
| 0x43f7a8 | Reverb | 1 type, 2 pre-delay, 3 time ("Damp" in the name list 0x409000 has no address) | type 0-4 for ids 2-3 | |
| 0x43f780 | Patchbay | id = output pair 0-4 | 0 | value = wire + 1 |
| 0x43f7d8 | AUTO-SENS | 5+c = channel select flag | 0 | |
| 0x43f8c0 | System | 14, 16 exposed (read-only) | 0 | name switch 0x30d21c |
| 0x43f85c | Clock | 0 (rate idx?), 1 (?) | 0 | read-only |

**Registry** (C):
- Built at boot by fw 0x322378 into 0x441a14 as {flag, count=49, ptr[]}. Each entry is 20 bytes: +0 cache, +4 type, +8..0xB address pattern (FF = wildcard), +0x10 vtable.
- Vtable slots: +0x14 get, +0x1c set, +0x24 action, +0x2c hi-dim, +0x34 lo-dim, +0x3c param id.

**Change notification** (C):
- Each periodic tick, fw 0x320574 -> 0x32036c compares every value with its cache. Each difference goes out as a DT1 to the concrete address (0x31fad8 -> 0x31fa2c).
- This covers front-panel edits **and** remote edits. Whether a remote write updates the cache first (no echo) or not (echo) is unknown (I).

---

## 5. What a replacement app must do

| Step | Action | Basis |
|---|---|---|
| 1 Open | Open the CTRL MIDI in/out port (cable 1). SysEx input buffers must be at least 1200 bytes (the dump is 1159 B). Close the Roland panel first. | C sizes / I port |
| 2 Identify (optional) | Send `F0 7E 10 06 01 F7`, expect `... 06 02 41 4D 02 00 ...`. Do not use device 7F. | C |
| 3 Read state | Send the bulk RQ1 (section 2), parse the 1146-byte DT1 with the layout in 3.7. This is the **only** read mechanism. Re-request after any doubt (reconnect, init, restore). | C |
| 4 Hello | Optionally send `00 00 00 00` = 0, as the panel's device button does. | I |
| 5 Meters | Send `00 0A 00 00` = 1 while the meter view is visible and 0 on close or hide. Expect `00 0A 00 01` frames and keep the max per UI frame. Rate unknown; the panel polls the driver every 30 ms (I). | C |
| 6 Write | One DT1 per changed parameter, at the addresses in section 3, with the encoding by type. Clamp to the ranges yourself. Coalesce fader/pan drags to about 30-50 Hz per parameter (I, a sensible default). | C (format) |
| 7 Listen | Treat incoming DT1s as the truth and update the model. Expect each one twice if both cables are open (I). If the unit echoes your own writes, suppress echo-driven UI jumps during a drag (latest-wins with a short hold). | C (notify) / I (echo) |
| 8 Linked pairs | Write preamp LINK at the even channel. Mixer LINK uses i = 2*pair. Whether the unit mirrors the partner's values itself is unknown, so watch the notifications. | C / I |
| 9 Actions | Restore `00 00 00 01`=1, backup =0, init `00 00 00 02`=0..4, AUTO-SENS `00 02 01 04` mask then `00 02 01 05`. Re-dump after each. Ask the user before backup or init (they overwrite stored settings). | C / I |
| 10 PC-side only | Sample rate, buffer and latency (IOCTLs 0x222328/0x222340/0x22232c, 0x22203c..) and the PC stream meters never reach the unit as SysEx. Use the Windows audio APIs or ASIO, or skip them. | C |

Do **not** use for a GUI:
- The quick strip `00 06 ii 14` (ASIO direct monitor path; it hits MIX A only).
- The remote injector `F0 41 78` (it simulates keys).

---

## 6. Open questions and live-capture plan

### Open questions (ranked by importance for the app)

1. **Echo:** does the unit send back the PC's own writes (does the cache update in set or not)? This decides the feedback-loop handling.
2. **Message rates:** the period of the SysEx task's periodic tick (type-0 message to handler 0x31f4ec). It sets the meter rate and the notify latency.
3. **Cables:** do replies arrive on cable 1 **and** cable 2 (streams 0/1)? Does a DT1 sent on cable 1 behave exactly like one on cable 2?
4. **Fader scale:** the dB curve of the 24-bit VR / master / send / return values (is 0x200000 0 dB, and where is -INF?). Also the pan endpoints (0 / 0x4000 / 0x8000 vs 0x7FFF). Is master s = 0/1 L/R?
5. **Comp and reverb ranges:** the THRESHOLD and GAIN maxima, the reverb pre-delay/time steps and units per type, and the reverb type order.
6. **AUTO-SENS:** which bit is ch1? Does the unit report new SENS values as `00 05 0c 04` DT1s? What do 00 02 01 05 = 0 vs 1 do?
7. **Startup order:** the panel's startup order (bulk RQ1, meter on, 00 00 00 00). Is meter re-enable needed after reconnect or panel close?
8. **Blocks 08/09:** does the panel ever write block 09 (ids 145/146, LINK OUT)? What do 08 m0 00 vs 08 m0 0D mean in the UI?
9. **Write-only system addresses:** the meaning of `00 00 00 00` (lcd ioctl 4), and the unnamed status 00 01 00 00 / 00 02 00 00 / 00 02 00 02.
10. **Hi-Z and LINK:** are Hi-Z writes on ch 3-8 accepted? How does preamp LINK propagate to the odd partner?
11. **Bulk bit order:** the t1/t2 bit order inside the bulk bytes, and decoder 0x320186 (type 5) read in full.

### Live-capture plan

Run this only with the user's go, one step at a time. Nothing here was done.

**Capture method A (preferred, no firmware change):**
- USBPcap + Wireshark on the OCTA-CAPTURE's USB device, filtering the MIDI bulk endpoints.
- It sees both directions of the **Roland panel's** traffic (driver -> cable 2, unit -> cables 1 and 2) and timestamps for rates.

**Capture method B (fallback):**
- A lab-hook "copy mode" that copies (does not eat) `F0 41 10 00 00 4D` and `F0 41 78` traffic into a ring buffer, read with a new F0 7D 4F command.
- This is a firmware change and needs its own review.

| # | Action (panel or front panel) | Expect / measure | Answers Q |
|---|---|---|---|
| 1 | Start the capture, then launch the Control Panel | Startup sequence: RQ1 01, 00 0A 00 00=1, 00 00 00 00? Dump timing | 7, 2 |
| 2 | Idle 10 s with the meters showing, signal on IN1 | Meter DT1 period; duplicate frames on cable 1 and 2 | 2, 3 |
| 3 | Panel: SENS ch1 0 -> 30 dB step by step | Write `00 05 00 04 3C`; any echo from the unit and its delay | 1 |
| 4 | Front panel: turn SENS ch1 | Unit DT1 on both cables, notify latency | 3, 2 |
| 5 | Panel: drag the MIX A IN1 fader from bottom to top, read the dB label at stops (-INF, -20, 0, max) | 24-bit value vs dB pairs | 4 |
| 6 | Panel: pan IN1 full L, centre, full R | 16-bit endpoints | 4 |
| 7 | Panel: master fader A L/R with the link off, then on | s = 0/1 mapping; any block 09 writes | 4, 8 |
| 8 | Panel: sweep comp THRES/GAIN to max; every reverb type, pre-delay and time to min/max | Max raw values vs labels | 5 |
| 9 | Panel: preamp LINK on ch1-2, then change SENS on ch1 | What is sent for ch2 (unit-side mirror?) | 10 |
| 10 | Panel: AUTO-SENS on ch1 only, let it finish; repeat with cancel | Mask bit order; 00 02 01 05 value; resulting SENS DT1s | 6 |
| 11 | Panel: Backup, Restore, Initialize patchbay (on a state the user is willing to lose; ask first) | 00 00 00 01 / 02 values; a re-dump follows? | 9 |
| 12 | Close the panel | Meter off (00 0A 00 00=0)? | 7 |

After the capture: diff the dumps taken before and after each step against the 3.7 layout to confirm the bulk bit order (Q11).
