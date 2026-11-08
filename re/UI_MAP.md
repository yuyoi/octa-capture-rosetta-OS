# OCTA-CAPTURE v1.51 main: UI map + Rosetta STRIP page proposal

Offline RE only (stock/main.bin, re/main.dis, runtime data via tools/build_splash_main.py run()). Nothing flashed, nothing committed.
Merged from four mapping passes (screens, input, display, persistence), each checked by an independent verifier.

Tags: **C** = CONFIRMED and re-checked by a verifier. **C\*** = CONFIRMED by the mapper (address seen in code/data), not re-checked.
**I** = INFERRED. Claims a verifier refuted are dropped or shown in their corrected form (marked "fixed").

## 1. TL;DR

- **Screen system:** table 0x43a980 has 52 screens (0-30 UI, 31-51 test mode). Each screen is a C++ object with a vtable at [obj+0x880]. The manager sends it event types 1 create / 2 destroy / 3 pre-draw / 4 Draw / 5 KeyDown / 6 KeyUp / 7 Encoder / 8 tick (50 ms) / 0x21 Command. C
- **Navigation:** HOME = screen 1 'INPUT'. GAIN_SW on the Channel page (2) opens AUTO-SENS setup (18). Holding DISPLAY for 1 s opens SYSTEM (3). Reverb and Patchbay are buried in the SYSTEM list. C
- **Controls:** 10 physical keys (codes 0-9: CH_L CH_R PH48V COMP DISPLAY GAIN_SW CURSOR MIX_SEL POWER PHONE_SEL) and 3 encoders (0 GAIN, 1 CURSOR, 2 MONITOR). Codes 10-18 are virtual (remote only). The names are C. The mapping to the printed panel labels is I.
- **Meters:** the base tick copies the per-channel peaks every 50 ms on every screen: in 0x43ed10[10], out 0x43ed38[10], DAW 0x43ed60[10] (Q27 peak, read-and-reset) and compressor GR 0x43ed88[8] (u16, 0x7fff = no GR). This is a free source for an always-on VU and for idle detection. C
- **Drawing:** Graphics [obj+0x28] / [0x440944] on a 128x64 1bpp surface [0x445300]. The API (Clear, Fill/Erase/InvertRect, Line, SetFont, DrawString, DrawBitmapId) is mapped and used by every stock screen. C
- **Persistence:** 0xD1C-byte block at flash 0xF0040 with no spare bytes. Every save rewrites the whole 64 KB sector 18. The only clean spot for strip settings is a CRC'd record in the unused sector-18 tail (e.g. 0xFF000), written inside Roland's own save. That needs an explicit exception to FLASH_SAFETY rule 7. C (layout) / I (plan)
- **STRIP page hook:** post-scan and ARMED-gated, ui_hook overwrites the method words of screen 18's vtable 0x402418, which nothing else uses. GAIN_SW on Channel then opens our STRIP page, with the channel already in [0x419000]. No table, handler, init-data or pre-scan change. The same trick on HOME vtable 0x401ca8 gives compact meters and the idle Hex girl (bitmap 0xf5). I (resting on C facts)
- **De-risk first:** prototype the whole page live over the lab link (write vtable words + uploaded code) before any flash.

## 2. Screens + menu tree

### 2.1 Screen table and object model
| item | value | tag |
|---|---|---|
| table | 0x43a980, 16 B/entry {u32 desc*, u8 a, s8 group, s16 b, u32 x, u32 mask}; idx math 0x358712-0x35871c; end marker {0,-1,0,0} at 0x43acc0 | C (fixed: x=0x1f for 0-30, 0 for 31-51; mask=7) |
| entry fields | screens 0-30: a=10..80, group=-1, b=-1 (screen 0: a=0, b=0). Screens 31-51: a=0x1c, group=2, b=0. group>=0 and != W[0x419a60] -> 0x30d9a4. b>=0 -> 0x3581ac. +8/+0xc -> [0x419a70]/[0x419a74] at 0x3591e0 | C; meaning of 'a' I |
| descriptor | {0, handler, 0, 0, 0}; manager calls handler(R0=event*) via [desc+4] (0x35872e, 0x359a58, ...) | C |
| handler shape | ev1: malloc 0x325a76 (0x884-0x910 B), memset, [obj+0x880]=base vt 0x4001e0, [obj]=title, 0x33eaac(obj+0x2c), [obj+0x880]=class vt, global=obj, dispatch. ev2: dispatch, teardown, free 0x3259f8. Else dispatch only. Example: 0x319a4c-0x319b6e | C |
| virtual call | P1=[this+0x880]; R0=this+W[P1+8k]; CALL [P1+8k+4]. All this-deltas 0 | C |
| base dispatcher | 0x302a34, jump table 0x41c370 (39 types) | C |

Vtable slots (fn word at vt+8k+4):
| k | fn off | event | base path | tag |
|---|---|---|---|---|
| 1 | +0x0c | OnEvent (= 0x302a34) | | C |
| 2 | +0x14 | type 0 | 0x302a70 | C |
| 3 | +0x1c | OnCreate | type 1 -> 0x302480 | C |
| 4 | +0x24 | OnDestroy | type 2 -> 0x302a98 (also 0x33e720, frees canvases) | C |
| 5 | +0x2c | KeyDown(key) | type 5 -> 0x302b08 | C |
| 6 | +0x34 | KeyUp(key) | type 6 -> 0x30294c | C |
| 7 | +0x3c | pre-draw | type 3 -> 0x302ac6 (in gsave/grest) | C |
| 8 | +0x44 | Draw | type 4 -> 0x302ae2 (in gsave/grest, then child widgets 0x33e9f0) | C |
| 9 | +0x4c | Tick | type 8 -> 0x3025a4 (meters first) | C |
| 10 | +0x54 | type 9 | 0x302be0 | C |
| 11 | +0x5c | type 11 (p1,p2,p3) | 0x302c02 | C |
| 12 | +0x64 | postCreate | from 0x302480 | C |
| 13 | +0x6c | Encoder(id, delta) | type 7 -> 0x302b78 (id=W[ev+2], delta=W[ev+4]) | C |
| 14 | +0x74 | Command(code) | type 0x21 -> 0x302c7e; code>=10000 dropped here, then unconsumed -> global 0x30db56 (from 0x358d6c) | C (fixed) |
| 15-17 | +0x7c/+0x84/+0x8c | types 36/37/38 | | C |

Method ABI: R0=this, R1/R2=args. Return R0!=0 = consumed; 0 lets the base default run. I (from use)

Base create 0x302480: canvases [this+0x28] (drawn, LCD surface via 0x34f31c) and [this+0x24], rect (0,0,127,63), widget root 0x33e4c4(this+0x2c), OnCreate, postCreate, [+0x18]=0, W[+0x16]=20 (DISPLAY hold ticks), 0x35890e(50) (tick period). It also sets 0x43ed98[0..7]=1, 0x43ee28[0..9]=0, 0x43edb8[0..1]=0, [this+8]=1. C (fixed: does not clear 0x43ed98)

### 2.2 Screen manager
| API | address | effect | tag |
|---|---|---|---|
| SWITCH(n) | 0x358678 | W[0x419a5c]=n, flags, signal 0x33f904(0x445200) | C |
| BACK | 0x3586b8 | W[0x419a5c]=-2; pops history u32 stack 0x44585c idx W[0x445440]; prev screen -> W[0x419a5a] | C |
| GetCurrent | 0x3585b8 | W[0x419a5e] | C |
| Redraw | 0x3585ec | [0x419a54]=1 -> bit14 | C |
| Recreate flag | 0x358604 | [0x419a50]=1 -> bit15 | C |
| SendToCurrent | 0x3586e4(-100 or idx, ev) | only if W[0x419a58] (constructed) == W[0x419a5e] | C |
| processor | 0x359050 | runs once per tick and after every press/encoder event. bit15: type 2 old, type 1 new (construct 0x358938), type 3. Draw (type 4, 0x35932c) when ([0x419a6c] & [0x419a74]) != 0, then present 0x35a220 + 0x30ddc8 | C (fixed: Draw fires on any pending bit, not only bit14) |
| request -3 | 0x359386 | destroy + reconstruct the same screen, no pop. No writer found | C (fixed) |
| tick period | W[0x445448] reload / W[0x445446] countdown, set by 0x35890e. Not a history index | C (fixed) |

UI tick 0x359958 (from 0x35a9f4 <- UI loop 0x30e198): period check first (0x359976) -> 0x33fbc4 -> 0x359050 -> widget timers on [0x4451fc] -> 0x30dd5c(50) -> popup 0x35a1c4(0x4458f0) -> **CALL 0x30ddc8 at 0x359a16 (our ui_hook)**. Then the loop drains messages (0x35abd4) and presents 0x30e650. C

### 2.3 Screen list (0-30)
| # | title | role | vtable | handler | global | tag |
|---|---|---|---|---|---|---|
| 0 | NO NAME | boot splash (bitmap 0xf5) | 0x4025e0 | 0x319a4c | 0x419024 | C |
| 1 | INPUT | HOME meters (pages INPUT/DAW/OUTPUT) | 0x401ca8 | 0x3148a4 | 0x418f3c | C |
| 2 | Channel | PRE-AMP channel page | 0x401538 | 0x3109f8 | 0x418c54 | C |
| 3 | SYSTEM | system list | 0x402a08 | 0x31afcc | 0x419058 | C |
| 4 | Initialize | | 0x400148 | 0x30207c | 0x408d48 | C |
| 5 | NO NAME | LEFT/RIGHT | 0x402bd0 | 0x31b9c4 | 0x419064 | C |
| 6 | NO NAME | POWER OFF / 'Saving Parameters' | 0x402548 | 0x319484 | 0x419018 | C |
| 7 | MIX %c | mixer | 0x401e58 | 0x31594c | 0x418f48 | C |
| 8 | MIX %c - CH %d | mixer channel | 0x402008 | 0x3166e8 | 0x418f50 | C |
| 9 | MIX %c MASTER | mix master | 0x402250 | 0x3179e8 | 0x418f70 | C |
| 10 | CH %d  COMP | comp (duplicate, no panel route) | 0x401780 | 0x311c4c | 0x418d1c | C |
| 11 | Mixer%c | (no static route) | 0x4020a0 | 0x316b7c | 0x418f64 | C |
| 12 | PATCHBAY | output routing | 0x4022e8 | 0x317d3c | 0x418f80 | C |
| 13 | REVERB | 'RTN' | 0x4024b0 | 0x3190c8 | 0x419014 | C |
| 14 | INITIALIZE | ALL/MIC PRE/DIRECT MIX/REVERB/PATCHBAY (5 items, 0x418f1c) | 0x401af8 | 0x3137c4 | 0x418f18 | C (fixed) |
| 15 | BACKUP ALL PRMS. | | 0x401388 | 0x30f988 | 0x418c48 | C |
| 16 | RECALL ALL PRMS. | | 0x4012f0 | 0x30f620 | 0x418c44 | C |
| 17 | CLEAR MUTE SOLO | | 0x4015d0 | 0x310f04 | 0x418d08 | C |
| 18 | AUTO-SENS | setup / channel select (alloc 0x890) | **0x402418** | 0x318b34 | 0x419004 | C |
| 19 | AUTO-SENS | run (MANUAL / timers, alloc 0x8e0) | 0x402380 | 0x318540 | 0x418ffc | C |
| 20 | CH %d  COMP | comp (panel route) | 0x401930 | 0x312cb0 | 0x418df8 | C |
| 21 | VERSION | | 0x402aa0 | 0x31b370 | 0x41905c | C |
| 22 | void | blank | 0x402b38 | 0x31b568 | 0x419060 | C |
| 23 | DETAIL VERSIONS | | 0x402678 | 0x319f1c | 0x419028 | C |
| 24 | SYSTEM2 | | 0x402710 | 0x31a22c | 0x419034 | C |
| 25 | HEAP Check | debug | 0x4027a8 | 0x31a54c | 0x419040 | C |
| 26 | Fader Touch Sens. | VS-100 leftover | 0x402840 | 0x31a748 | 0x419044 | C |
| 27/28 | DEBUG PAGE4/5 | debug | 0x4028d8 / 0x402970 | 0x31aa1c / 0x31ac44 | 0x419048 / 0x41904c | C |
| 29 | EXCEPTION | | 0x401a60 | 0x313274 | 0x418f10 | C |
| 30 | SYSTEM ERROR | | 0x4019c8 | 0x313024 | 0x418ebc | C |

Test screens 31-51: base vt 0x4074a0 (screen 34: own base 0x407da0), derived 0x4075a0..0x408ab8, handlers 0x35c5bc..0x3616b0, menu table 0x43b2f8. C (fixed)
Role labels in the 'role' column are I, from strings and navigation.

### 2.4 Menu tree
```
boot: screen 0 --Command(0) 0x3199f2--> 0x302444 --> HOME 1   (error: 0x312e80 -> 30)        C
HOME 1  [CURSOR enc: page INPUT/DAW/OUTPUT, [0x408d64], 0x314744]                          C
 |- any unconsumed CH_L/CH_R/PH48V/COMP/GAIN_SW, or GAIN enc ---> Channel 2   (0x302cf0, 0x302bce)
 |- MIX_SEL ---> MIXER 7 (0x302d04) --> 9 MASTER (0x31591c) / 8 CH (0x315924); 9 --> 17 CLEAR MUTE SOLO
 |- MONITOR enc ---> MIX MASTER 9 (0x302bc4)
 |- DISPLAY short (acts on release) ---> HOME (refresh in place if already HOME)
 '- DISPLAY hold 1 s ---> SYSTEM 3; hold again inside 3 ---> SYSTEM2 24
Channel 2  [ch 0x418c50, row 0x418c4c]
 |- CH_L ch-- (stops at 0)   CH_R ch++ (wraps to 0 at [this+0x890])  -> in-place recreate     C (fixed)
 |- PH48V toggle param 0xb   |- COMP: set param 10, -> COMP 20 (0x3108dc)
 |- CURSOR push: activate row (list 0x418c58 for ch 0/1, else 0x418cb8)
 '- GAIN_SW: [0x419000]=ch (0x3186d8) -> AUTO-SENS setup 18 (0x3108ea)
AUTO-SENS 18 --GAIN_SW (0x318a50) or CURSOR (0x318914)--> AUTO-SENS run 19 --> Channel 2 (0x318312.. 0x318534)
   remote/USB: A-S controller virtual 0x32227c (vt slot 0x40392c) posts 0x2713 -> global 0x30d890 -> 19
SYSTEM 3 (13-row list, records 0x435b04, cursor [0x440e14], jump table 0x435c50):
   0 LCD CONTRAST | 1 LCD REVERSE | 2 REVERB -> 13 | 3 PATCHBAY -> 12 | 4 LCD BACKLIGHT |
   5 BACKUP ALL PRM. -> 15 | 6 RECALL ALL PRM. -> 16 | 7 VS EXPAND (no page) | 8 ENC ACC SENS |
   9 CLEAR MUTE/SOLO -> 17 | 10 POWER OFF (auto-off OFF/4HOURS) | 11 INITIALIZE -> 14 | 12 VERSION -> 21
SYSTEM2 24 (cursor 0..6) -> 25-28 via list-widget item targets                              I
Global commands: 0x2712 -> POWER OFF 6 (0x30d8be); 0x2713 -> AUTO-SENS run 19 (0x30d8b0)    C
Secret: INITIALIZE key sequence CH_R,CH_R,CH_L,CH_L,CH_R,CH_L,CH_R,CH_L (0x435248) -> DETAIL VERSIONS   C*
```
AUTO-SENS params: A-S MARGIN 0x41d10c, A-S CH Fix 0x41d11c, MANUAL/30 SEC/1/3/5 MIN table 0x4094a4. Param object 0x43f7d8 (= settings grp4), ch flags idx 5..12, set 0x301a88. C (strings); row mapping I.
Effects/routing strings: mixer LINK/PAN/MONO/SEND/SOLO/MUTE/M VR/RETRN 0x41ca94-0x41cae8; OUT1/2..OUT9/10, MIX A-D, WAVE 1/2..9/10 0x41cd9c-0x41ce1c; reverb OFF/ECHO/ROOM/S.HALL/L.HALL 0x41cfcc; P.Dly/Time/Damp 0x41ce48. C*

## 3. Input

### 3.1 Controls
| code | name (table 0x419af0) | source | LED (test table 0x43c1b4) | tag |
|---|---|---|---|---|
| 0 | CH_L | extio bit 1 | 0 | C name; panel label = PRE-AMP [<] I |
| 1 | CH_R | extio bit 3 | 1 | C; [>] I |
| 2 | PH48V | extio bit 5 | 2 | C |
| 3 | COMP | extio bit 7 | 3 | C |
| 4 | DISPLAY | extio bit 9 | 4 | C |
| 5 | GAIN_SW | extio bit 10 | -1 (no LED) | C; = GAIN encoder push I |
| 6 | CURSOR | extio bit 11 | -1 | C; = CURSOR/VALUE push I |
| 7 | MIX_SEL | extio bit 13 | 5 | C |
| 8 | POWER | gpio 47 (PH15), high = pressed | | C |
| 9 | PHONE_SEL | gpio 46 (PH14), polarity unknown | | C / polarity ? |
| 10-18 | LEFT RIGHT UP DOWN INC DEC ENTER EXIT SHIFT | not from the panel (sw has 10 keys). Remote injector 0x30e024 and a few synthetic posters (0x30dbca, 0x35a588) | | C (not panel); origin I |

Encoders (names 0x419ae4): 0 GAIN gpio {27,26}, 1 CURSOR {31,30}, 2 MONITOR {25,23} (config 0x408e00). C. Port letters (PG11/10, PG15/14, PG9/7) I.
Sw-key index == logical code: I (POWER = 8 in both, and the LED test table fits). Settle on stock hardware with the test-mode switch test.

### 3.2 Drivers
- **sw:** device 0x41c644 (name 0x41c48c, vt 0x419fc4, cfg 0x408e3c with 7 words), init 0x368760, ioctl 0x368944 (table 0x43da8c: 0 set cb + force re-report + start cyclic, 1 stop, 2 period ms/5, 3 last state, 4 init state), state 0x448964. The cyclic 0x368658 has period 5 and scans every 4th tick (about 20 ms). It is created stopped and only ioctl 0 starts it. Extio keys are active-low. No debounce beyond sampling. Callback(R0=key, R1=state). C
- **enc:** device 0x41c638 (vt 0x419f7c), init 0x3681dc (encoders disabled until ioctl 0), ioctl 0x367eec, state 0x4487bc. The cyclic 0x3680c4 runs every tick (about 1 ms) through quad machine 0x368002 / table 0x43d988, and every 10 ticks calls cb(enc, accel(count)). C
- **Acceleration** 0x367fd8: d' = |d|*(acc+128)>>8 (sign kept), table 0x43d938. Decay runs every tick (about 1 ms), not every 10 ms. ioctl 2: 0 = off, 1..10 = table row, -1 = off AND lock [+0x168]=1 (later ioctl 2 calls are ignored). Args <= -2 index before the table (latent bug). Set from system param 3 ENC ACC SENS at 0x30d5a2. C (fixed)

### 3.3 Event path
```
sw cyclic --cb 0x30d7f8--> post 0x35a620 (type 5 press) / 0x35a520 (type 6 release)          C
enc cyclic --cb 0x30d81c--> post 0x35a8b0 (type 7, W+2 enc, W+4 delta)                        C
   ring 0x4459b8, 256 x 20 B, write idx [0x419b40], read idx [0x419b3c], drops when full, no lock   C
UI task 0x30e198: wai_flg(2, 0x21ff) 0x30e25c -> loop 0x35abd4 -> RAM jump table 0x43ae58       C
   [0x43ae6c] type 5 -> 0x359854 press: bitmap 0x44544c, test flag [0x41901c] -> stub 0x30d964,
             HMI busy 0x33f904(0x445200) drops the key, widget event 10 0x32e788 -> else 0x35a400
             -> 0x3586e4(-100) screen KeyDown -> else global 0x30d9d0 (EXIT -> BACK 0x30da28)
   [0x43ae70] type 6 -> 0x35979c release -> event 11 0x32e7dc -> 0x35a43c -> screen KeyUp -> 0x30d958 (empty)
   [0x43ae74] type 7 -> 0x359a70 encoder -> event 12 0x32e830 -> screen handler [desc+4](msg) directly
   type 12 -> 0x3596ac auto-repeat
   0x359050 runs after every dispatched press/encoder event                                     C
```
- **Widget layer:** focus [[0x4451fc]+0x64], parent [+0x20]. Pre-filter vt+0x50/0x54, then table 0x437cc4. Key down vt+0xac, up vt+0xb4, encoder vt+0xbc, each gated on [w+0x58]. C
- **Screen defaults when a method returns 0:**
  - KeyDown (table 0x41c350): keys 0,1,2,3,5 -> SWITCH 2; key 4 arms the DISPLAY hold; 6 none; 7 -> SWITCH 7. W[0x43ed0c]=key.
  - Encoder: 0 -> 2, 2 -> 9, 1 none.
  - KeyUp: DISPLAY acts on release (refresh HOME or SWITCH 1 when [0x4094f0] is 0/1); PHONE_SEL is eaten.
  - C
- **Power activity:** types 5, 6 and 7 always run open('power') ioctl 6 close (auto-off reload). Type 8 runs it only when a 0x43ed10 entry > [0x43ed08] or 0x302e6c()!=0, gated by the [0x408d6c] ms accumulator. C (fixed)
- **Auto-repeat:** partner map 0x4458dc (19 B), 500 ms delay / 100 ms rate, "10 ms fast" really 50 ms (UI tick countdown 0x30e2c4). A partner press is re-sent as the held key; the partner release is swallowed. Repeat flag W[0x419a62]. C
- **Remote injector:** UI onReceive 0x30e128 -> 0x30e024 (cmd table 0x41d600: 1 release, 2 press, 4 encoder, ...). Sender task I.

### 3.4 Boot combos (scan 0x31bc0c, ioctl 4; tables 0x435d50-0x435da0, handlers 0x435db0)
{5,5,5} GAIN_SW checked first (not held -> side block 0x31bd48 runs). {4,2,0} -> mode 1, {4,3,0} -> mode 2, {6,0,0} -> 3 = [ALL] (NEVER), {6,1,1} -> 3 normal updater (recovery), {0,1,1} -> 4 factory reset. C (data/code); button names I (CURSOR push + CH_R = updater).

### 3.5 Gain path (for the STRIP GAIN block)
- **Channel encoder 0x310958:** GAIN enc calls 0x301670(0x43f444, param 0 SENS, dir, ch) |delta| times. Each call does getParam 0x3097e4 / getRange 0x30987a, +/-1, clamp, setParam 0x3099d0. C*
- **setParam 0x3099d0:** channel obj 0x43eea8+24*ch (9 ch), vt+0x2c, then observers. Channel method 0x304538 sends -f*16 to 'acon' (ioctl 0 ch, ioctl 3 value). C*
- **acon ioctl3 0x36523c:** picks one of 4 analog ranges, thresholds 11/22/33, 2 extio bits per channel, range-change callback [0x446f84+0x20]. C*. The fine gain is DSP trim W[0xff800158+2*ch]: I (writer not found).
- **Preamp param ids** (names 0x41c850): 0 SENS, 1 LINK, 2 THRS, 3 RATO, 4 GAIN, 5 ATK, 6 REL, 7 GATE, 8 Locut, 9 Phi, 10 Comp, 11 48V, 12 Hi-Z. C

### 3.6 Where to intercept (all post-scan)
| option | how | tag |
|---|---|---|
| **A. screen methods (preferred for the page)** | our KeyDown/KeyUp/Encoder in the hijacked vtable get every key the widget layer does not eat. Return 1 to swallow, 0 for stock defaults | I (on C dispatch) |
| B. driver callbacks | from ui_hook repeat 0x30d830 with our cb: open 'sw' 0x41d59c / 'enc' 0x41d5a0, ioctl 0, close. Our cb tail-calls 0x30d7f8/0x30d81c. Runs in cyclic context. Re-reports all keys once | I |
| C. RAM jump table | rewrite 0x43ae6c/70/74 (msg at FP-0x14, end JUMP 0x35ac5c) | I (writability unverified) |
| D. read-only | poll bitmap 0x44544c; no encoders, cannot swallow | C |

## 4. HOME screen, drawing API, meters

### 4.1 HOME (screen 1, vt 0x401ca8, obj 0x8bc)
- **Slots:** KeyDown 0x401cd4 = 0x3138f0, Draw 0x401cec = 0x314460, Tick 0x401cf4 = 0x314814 (redraws every tick via 0x3585ec at 0x314890), Encoder 0x401d14 = 0x314744 (CURSOR pages mod 3, always returns 0). C* slot words; C Encoder logic.
- **Pages:** [0x408d64] 0 INPUT / 1 DAW / 2 OUTPUT. Titles 0x418f30. Meter sources 0x4352c0 (0x43ed10 / 0x43ed60 / 0x43ed38). The right-hand pair always shows outputs 0x43ed38[0..1]. C
- **Draw 0x314460:** Clear; font 6 title at (12,0); line (0,18)-(128,18); MASTER bitmap 0xe2 at (107,57) under the pair; icons via 0x34ea14 at (0x4c,1) id 0x100 if 0x302e6c(), (0x72,1), (0x5c,10) sample-rate badge 0x43529c[...], (0x72,10), (0x4c,10) 0xf6. INPUT page only: digits '1'+i font 13 at (x0+1, y1-8). A flagged channel (0x3097e4(0x43f444,0xb,i) = 48V) gets SwapColors + a vertical line at x0, plus bitmap 0xff (9x20) at (0,45). C (fixed)
- **Meter widgets:** 10 (or 4 when 0x30beba(0x43f85c,0,0)==3) children of 0xa0 B at obj+0x88c, rect (0,0,8,46) placed at (10+9i, 19), so y 19..65 (2 rows off-glass). The pair is at x 107/116. C
- **Widget draw 0x313ba0:** 6 segments at -42/-24/-18/-12/-6/-2 dBFS (float compares), 4 px segments with 2 px gaps. Hold 20 ticks (100 on clip), then down one segment every 6 ticks. Own 50 ms timer 0x3140a6. Mode +0x94 feeds the same bar from 0x32551c() * [+0x98]. C (fixed)

### 4.2 Meter data
| array | content | source | tag |
|---|---|---|---|
| 0x43ed10[10] u32 | input peak, Q27 (2^27 = FS), read-and-reset each 50 ms | L1 latch {count,ptr} [0xff801650]; latch inside Roland chain 0xffa080bc: post-trim, pre-polarity/low-cut/comp (so post-strip) | C; post-strip I |
| 0x43ed60[10] u32 | DAW (USB play) peak | [0xff801660] | C |
| 0x43ed38[10] u32 | output peak | [0xff801658] | C |
| 0x43ed88[8] u16 | comp gain, min-held, 0x7fff = no GR | [0xff801648], reset to 0x7fff | C |
| 0x43ede8[8] | peak hold max(in>>13), clamp 0x3fff | 0x30274a-0x302778 | C |

- **Copy:** base tick 0x3025a4 on every screen, every 50 ms. No release multiply: R1.L=0 at 0x3025d8 clears the latch. The copy always runs ([0x408d5c] is only cleared). C (fixed)
- **Zeroing:** all arrays read 0 (GR 0x7fff) while W[0xff8003ac] != 0x2000 (output gain ramp/mute, L1 0xffa00738). C
- **Existing signal-present test:** 0x3028dc (any 0x43ed10 > [0x43ed08]). C

### 4.3 Drawing API (R0 = Graphics g, then R1, R2, [SP+0xc], [SP+0x10]; rect = 4 x s16 {x0,y0,x1,y1} inclusive)
| fn | addr | note | tag |
|---|---|---|---|
| Clear(g) | 0x34e0d4 | fill bounds with bg, marks dirty | C |
| SetColors(g,fg,bg) / Swap(g) | 0x34dee2 / 0x34dec2 | 0x01000000 ink, 0x01000003 paper; 0x01000001 coerced to ink; >=0x01000002 -> paper | C (fixed) |
| gsave / grest | 0x34df50 / 0x34df02 | fg/bg pair only, 16 levels | C |
| DrawLine(g,x0,y0,x1,y1) | 0x34dd82 | | C |
| DrawRect / FillRect / EraseRect / InvertRect (g,&r) | 0x34e550 / 0x34e5fe / 0x34e6aa / 0x34e756 | | C |
| SetClip / GetClip | 0x34e162 / 0x34e146 | | C |
| SetFont(g,id) | 0x34dfdc | id >= 19 ignored | C* |
| DrawString(g,x,y,s) | 0x34edd8 | font id [painter+0xc], \n ok, returns width | C |
| DrawText(g,x,y,s) | 0x34eb60 | alt font [painter+0x10] | C |
| StringWidth / StringHeight | 0x34ee38 / 0x34f038 | | C |
| DrawBitmapId(g,x,y,id) | 0x34e958 | blit + invert. Use for 0xf5 (screen 0 does (0,0,0xf5) at 0x31999a) | C |
| BlitId(g,x,y,id) | 0x34ea14 | blit only (reverse badges) | C; polarity I |
| sprintf | 0x324010 | | C* |
| invalidate all | 0x3408dc([0x445300]) | | C* |

- **Surface [0x445300]:** 128x64, 16 B/row, MSB = left, bit 0 = ink (the driver XORs). Present 0x30e650 -> LCD_Update 0x3481f0 sends dirty rows -> 'lcd' driver 0x36719c (scatter to 0x447184) -> LCD task 0x30e520 flushes on a 50 ms cyclic. C
- **Fonts** (table 0x418bec, ids 0-18): 0 'S' 6x7 fixed, 6 'L6' 9x21 (HOME title), 12 'S1' 6x6, 13 'S2' 7x7 (HOME digits), 18 'FIX12' 6x10 fixed (default; 21 chars x 6 rows), 1/3 large bold digits. C*
- **Bitmaps** (table 0x434150, 0x106 x 12 B, lookup 0x30e8d8): 0xf5 128x64 boot logo (= Hex girl in Rosetta builds, BOOT_SCREEN_PLAN.md), 0xe2 18x8 MASTER, 0xff 9x20, 0xe5-0xed rate badges. C
- **Idle/screensaver:** no screensaver or idle page exists (I: no such draw path found). LCD BACKLIGHT and POWER OFF / 4HOURS are real SYSTEM rows 4/10 (names via switch 0x30d21c, settings grp1 idx 1/0xF). The display pass's claim that these strings are unreferenced is contradicted by the persistence verifier. The LCD driver ioctl table 0x43d7e4 has no backlight ioctl. C*

## 5. Persistence

### 5.1 Settings block (flash 0xF0040, 0xD1C B, live RAM [0x419114], defaults [0x419118], state 0x41910c)
- **Table:** 0x435e0c, 52 x 16 B {flags, idx<<16|group, sub base, default}. Element size Ta 0x435de4 {4,4,2,1}[f&3] x Tb 0x435df4[(f>>8)&7] x Tc 0x435dcc {1,20}[(f>>16)&7]. Packed, sum = 0xD1C exactly. C
- **Accessors:** getters 0x300f36 (collect) and 0x300ebc (defaults); setters 0x301016 (notify) and 0x30106a. C (fixed)
- **Group objects** 0x41c2c0: 0 0x43f890 (not saved), 1 0x43f8c0 SYSTEM, 2 0x43f85c clock, 3 0x43f444 preamp/comp, 4 0x43f7d8 AUTO-SENS, 5 0x43f7a8 reverb, 6 0x43f74c mixer, 7 0x43f780 patchbay. C (fixed)

| offset | flash | content | tag |
|---|---|---|---|
| +0x000 | F0040 | magic 0x55AAFF00 | C |
| +0x004 | F0044 | version 0x1F | C |
| +0x008..0x00F | F0048 | grp1: idx0 LCD CONTRAST u8, idx1 BACKLIGHT u32, idx0xD VS EXPAND, idx0xF POWER OFF, idx3 ENC ACC SENS. LCD REVERSE (idx2) not saved | C |
| +0x010..0x013 | F0050 | grp4 AUTO-SENS idx1-4 u8 (dead if A-S removed; keep) | C offsets / I meaning |
| +0x014..0x015 | F0054 | grp2 idx2,3 (clock/rate) | C / I |
| +0x016..0x115 | F0056 | grp3 idx0-7 u32 x 8 ch (SENS..GATE) | C |
| +0x116..0x13D | F0156 | grp3 idx8-12 u8 x 8 ch (Locut, Phi, Comp, 48V, Hi-Z) | C |
| +0x13E..0x142 | F017E | grp7 patchbay idx0-4 | C / I |
| +0x143..0x15B | F0183 | grp5 reverb type + 3 x 8 params | C / I |
| +0x15C..0xA4B | F019C | grp6 mixer (4 mixes, blocks of 20) | C |
| +0xA4C..0xCCB | F0A8C | duplicate copies of mixer idx 0x17 (entries 45/46 = entry 39), 640 B, last copy wins | C |
| +0xCCC..0xD1B | F0D0C | grp6 idx 0x8F-0x93 | C |

### 5.2 Write paths (extmem is the only flash writer; ops 0x419f5c)
| fn | writes | callers | tag |
|---|---|---|---|
| 0x31d160 | primary @0xF0040 | 0x3192b8 (power-off, after collect 0x3192b2), 0x31e3a4 (menu BACKUP) | C |
| 0x31d404 | user backup @0xF0040+len (=0xF0D5C) | 0x31e3aa | C |
| 0x31d27c | defaults + 8 x 0xFF over the backup header (2 flushes) | 0x31977e (load fail / factory combo), 0x35e794 (INITIALIZE) | C |
| 0x31d47c | ISRL 12 B @0xF0000 | test mode 0x35b1b0 | C |

- **Load 0x31d074:** reads into the live buffer, checks magic (0x31d0d2) and version (0x31d10e) only. No CRC, no length check. C
- **Boot:** on fail, [0x4190f0]=4, 0x31d27c, then memcpy. Both paths then apply all groups except 2 (0x31cd58) and then group 2 (0x31cbfc). Sleep 1000 ms only on success. C (fixed)
- **Driver:** whole-sector RMW cache (64 KB malloc). Close flushes if dirty: erase 0x368c98 + program 0x368d30 + verify, up to 4 tries. No compare before write. Read-only opens never erase. Reads are memory-mapped at 0x200F0000+off. C
- **Sector 18 map:** F0000-F000B ISRL, F000C-F003F free, F0040-F0D5B primary, F0D5C-F1A77 backup, **F1A78-FFFFF unused (58,760 B, preserved by the RMW)**. C. Real-unit tail content (0xDEAD or 0xFF): I.
- **Elsewhere:** every other sector is boot code or inside the updater sum [0x4000,0x1FFFE) / main sum [0x20000,0xEFFFE). C

### 5.3 Recommendation for strip settings
1. **v1: no flash.** Strip settings live in RAM (L1 data block), off at boot = stock sound. GAIN and COMP blocks edit Roland params, which Roland already saves (grp3). Ships with no rule-7 exception.
2. **v2, only with an explicit user OK for a narrow exception to FLASH_SAFETY rule 7:**
   - **Record:** 'STRP' magic, u16 ver, u16 len, u32 seq, payload, CRC32, at flash 0xFF000 (sector-18 tail).
   - **Write:** retarget CALL 0x31d160 at 0x3192b8 and 0x31e3a4 to one wrapper: open 0x366aa4('extmem',2), lseek 0x3667b0(0xF0040), write 0x366770([0x419114],[0x419110]), lseek(0xFF000), write(record), close 0x3667ec. One flush, same erase count and power-loss window as stock. Both sites are post-scan.
   - **Read:** at arm time by pointer from 0x200FF000. Bad CRC or no record = all off.
   - **Guards:** assert the seek constant and 0xFF000+len <= 0x100000. The backup only reaches 0xFF000 if len > 0x77E0.
   - **Status:** I (premises C).
   - Rejected: spare bytes in the block (none), growing the table (version bump -> one full reset + backup destroyed), a separate sector (none free).

## 6. What Roland does badly

| # | issue | addresses | tag |
|---|---|---|---|
| 1 | AUTO-SENS takes the GAIN_SW push on the Channel page | 0x3108e2-0x3108ea | C |
| 2 | SYSTEM is hidden behind a 1 s DISPLAY hold, SYSTEM2/debug behind a second hold | 0x302544, 0x3028c0-0x3028d6 | C |
| 3 | REVERB/PATCHBAY sit in a flat 13-row list with LCD settings; routing spread over 6 screens (7,8,9,11,12,13) | 0x435b04, 0x435c50 | C |
| 4 | Dead row VS EXPAND (no page) | 0x435c50 default 0x31af6e | C* |
| 5 | Compressor UI exists twice (10 and 20), only 20 reachable | 0x31101c-0x311fe0, 0x311d64-0x312e82 | C* |
| 6 | Any unconsumed CH/48V/COMP/GAIN_SW or GAIN turn yanks you to Channel; MONITOR yanks to MIX MASTER | 0x41c350, 0x302bc4/0x302bce | C |
| 7 | CH_L stops at 0 but CH_R wraps (inconsistent) | 0x3107b0, 0x310808 | C |
| 8 | Channel change destroys and rebuilds the whole page | 0x3107d2-0x310802 | C |
| 9 | Every screen switch mallocs/frees 0x884-0x910 B + two 0xa0 B contexts | 0x325a76/0x3259f8, 0x302480 | C |
| 10 | Full redraw every 50 ms even when nothing changed | 0x318b20, 0x3109e8, 0x314890 | C (0x318b20), C* |
| 11 | Meter math on every screen, but no permanent VU anywhere; GR only on the COMP page | 0x3025a4, 0x312966 | C |
| 12 | Meters: 6 coarse segments, int->float->/2^27 soft-float per meter per tick | 0x313bc6-0x313c84, 0xffa03270 | C |
| 13 | Peak latch cleared by multiply-by-0, not atomic with the ISR MAX (peaks lost) | 0x3025ee-0x30265a | C |
| 14 | Meters blank during any output-gain ramp/mute | L1 0xffa00738, 0x3026e0 | C |
| 15 | HOME title (font 6 + rule at y=18) uses ~30% of the glass for a word; meter rect runs 2 rows off-glass | 0x314476-0x31449e | C |
| 16 | Key latency up to ~70 ms: 20 ms scan + posts never wake the UI task (waits for the 50 ms tick) | 0x368658, 0x35a520/0x35a620/0x35a8b0, 0x30e25c | C (mechanism), I (number) |
| 17 | Event ring has no lock, write index stored before the payload, drops silently | 0x35a572, 0x4459b8 | C (race I) |
| 18 | Gain: \|delta\| full get/range/set/notify/acon open-ioctl-ioctl-close per detent, plus encoder acceleration = coarse jumps | 0x310958, 0x301670, 0x304538 | C* |
| 19 | 3 driver calls (open/ioctl/close 'power') per key/encoder event to reload auto-off | 0x302b08, 0x302b78, 0x30294c | C |
| 20 | Key scan opens/ioctls/reads/closes extio every 20 ms | 0x368686-0x3686b8 | C* |
| 21 | Repeat "10 ms fast" really runs at 50 ms | 0x30e2c4, 0x419a44 | C |
| 22 | Acceleration ioctl -1 locks the setting forever; args <= -2 read before the table | 0x367f36, 0x367f60 | C |
| 23 | Every save erases/programs the whole 64 KB sector, even if nothing changed | 0x367d5e, 0x368c98 | C |
| 24 | Primary, "BACKUP ALL" and ISRL share one sector: the backup gives no power-loss protection | 0xF0000-0xF1A77 | C |
| 25 | Load has no CRC/length; a length change silently reads the backup as params | 0x31d074 | C |
| 26 | Load fail / factory reset also wipes the user backup header (second erase) | 0x31d2c4-0x31d30c | C |
| 27 | Saved only at soft power-off or from the menu: pulling AC loses the session | 0x3192b8, 0x31e3a4 | C |
| 28 | Flash write errors only go to a debug print; the user still sees 'Save Complete' | 0x367bbc, 0x43d904 | C |
| 29 | 640 B duplicate mixer entries, saved and applied 3 times | 0x4360dc, 0x4360ec | C |
| 30 | LCD REVERSE not persisted | table 0x435e0c | C (I effect) |
| 31 | Size overflow only prints 'CPrmMemory Memory Over' and carries on | 0x31cffc | C |
| 32 | ~1000 virtual get/set calls per save/load instead of a memcpy | 0x31c6c0, 0x31ca44, 0x31cbfc | C* |
| 33 | VS-100 baggage: 2/8/16/32 bpp painters, RGB->grey, 10 8-bpp bitmaps, Fader Touch Sens., HEAP/DEBUG pages, 21 test screens | 0x34f17c, 0x434150 | C |
| 34 | 'Digital' preamp = 4 analog ranges (11/22/33 thresholds) + DSP trim; range switches can click | 0x36523c | C* (trim I) |
| 35 | Latent NULL deref in the present overlay path ([0x445304] never stored) | 0x30e684 | I |

## 7. PROPOSAL: Rosetta STRIP page

### 7.1 What the user gets
- GAIN_SW push (on Channel, where AUTO-SENS was) -> **STRIP grid**: channels 1-8, each with a mini VU and its gain. CURSOR turns pick the channel; CURSOR push enters it.
- **Channel editor**: ONE block on screen at a time, "n/7" in the header. Blocks: GAIN, HPF, TUBE, LOW, PEAK, HIGH, COMP.
  - Rosetta scrolling parameter list: one param per row, never squeezed.
  - Always-visible input VU + GR bars on the right edge.
- **HOME**: no big title, full-height compact meters, small 1-8 digits. When nothing has sounded for N s it shows the Hex girl (0xf5); any signal, key or encoder brings the meters back.
- AUTO-SENS dropped from the panel route. Its settings bytes stay (layout and version unchanged).

### 7.2 Layout (128x64, font 18 FIX12 6x10 unless noted)
```
editor                                          grid
+------------------------------------+--+--+   +------------------------------------------+
|CH3 PEAK                       4/7  |VU|GR|   |STRIP        1  2  3  4  5  6  7  8      |
+------------------------------------+  |  |   | (font 13 digits, selected cell inverted)  |
| On          ON                     |  |  |   | 8 mini VU bars (y 12..52, 2 px peak tick) |
|>Freq        2.40k   (InvertRect)   |  |  |   | bottom row: gain of selected ch, 48V/Hz  |
| Gain       +3.5 dB                 |  |  |   +------------------------------------------+
| Q           1.20                   |  |  |
| (list scrolls; arrow at x=110)     |  |  |
+------------------------------------+--+--+
 x 0..115 = 19 chars; VU x 118-121, GR x 123-126, y 0..63
```
- VU: integer thresholds on the Q27 peak (table, no soft-float), -60..0 dBFS over 64 px, 1 s peak-hold tick.
- GR: from 0x43ed88[ch] (Roland comp), top-down. Our TUBE/strip stages have no GR.

### 7.3 Blocks -> parameters
| block | rows | backend | status |
|---|---|---|---|
| GAIN | SENS, 48V, Hi-Z, Phase, LINK | Roland params 0/11/12/9/1 on 0x43f444 via 0x301670 / 0x3099d0 / 0x3097e4 / 0x30987a (hardware, observers and save stay stock) | API C*; exists |
| HPF | On, Freq, Q (+ Roland Locut row) | strip engine stage hpf (docs/STRIP.md) + param 8 | exists |
| TUBE | On, Drive, Tone, Mix | NEW engine stage (saturator) after HIGH; measure cycles first | not built |
| LOW / PEAK / HIGH | On, Freq, Gain, Q | strip engine stages | exists |
| COMP | On(10), THRS(2), RATO(3), GAIN(4), ATK(5), REL(6), GATE(7) | Roland comp params; GR from 0x43ed88 | API C*; exists |

### 7.4 Hook points
| id | where | what | tag |
|---|---|---|---|
| H0 | 0x31bd7a arm_stub / 0x359a16 ui_hook (existing, ARMED-gated, post-scan) | all new code reached only from here | C (AUDIO_PATH.md) |
| H1 | vtable 0x402418 (screen 18, only ref 0x318b58/0x318b5c) | once, from the first ARMED ui_hook tick: [0x402434]=OnCreate, [0x40243c]=OnDestroy, [0x402444]=KeyDown, [0x40244c]=KeyUp, [0x40245c]=Draw, [0x402464]=Tick, [0x402484]=Encoder. Keep the stock stubs 0x318670/0x318684/0x318690/0x3186a4/0x3186b8/0x3186cc and ev3 0x3186ee | I (vtable only-ref C) |
| H2 | entry | unchanged: Channel GAIN_SW 0x3108e2 -> 0x3186d8 ([0x419000]=ch) -> SWITCH(18) 0x3108ea | C |
| H3 | vtable 0x401ca8 (HOME) | [0x401cec] Draw, [0x401cf4] Tick, wrap [0x401cd4] KeyDown -> 0x3138f0 and [0x401d14] Encoder -> 0x314744 to reset the idle timer. HOME may be live when patched: set 0x358604 (recreate) right after | I |
| H4 | screen 19 vt 0x402380 (only ref 0x318564) | v1: leave stock (Control Panel A-S via 0x2713 still works). Option: OnCreate -> SWITCH(2) | I |
| H5 | meters | read 0x43ed10/0x43ed60/0x43ed38/0x43ed88 after the base tick (our Tick runs after 0x3025a4). Never touch the L1 latches | C |
| H6 | idle | all 0x43ed10[0..9], 0x43ed60[0..9], 0x43ed38[0..9] < 134218 (-60 dBFS Q27) for N ticks (e.g. 200 = 10 s). Freeze the count while W[0xff8003ac] != 0x2000 (arrays zeroed) | I |
| H7 | exit | BACK 0x3586b8 or SWITCH 0x358678(n); redraw 0x3585ec each Tick (always-on VU) | C |
| H8 | strip engine | write coefficient sets + `sel` flip (existing STRIP.md protocol), same as the lab does | C (STRIP.md) |

Page key map (design choices to OK):
| control | grid | editor |
|---|---|---|
| CURSOR enc | channel 1-8 | scroll rows / change value when editing |
| CURSOR push | enter channel | toggle row edit |
| CH_L / CH_R | channel -/+ (both wrap) | block -/+ (n/7) |
| GAIN enc | gain of selected ch (consume, else -> Channel) | gain of this ch, any block |
| GAIN_SW | back to Channel | back to grid |
| COMP | (consume) | jump to COMP block |
| PH48V | 48V of selected ch (stock meaning) | same |
| DISPLAY | return 0 on down (hold -> SYSTEM still works); release -> HOME (stock) | release: consume, clear [this+0x18], back to grid |
| MIX_SEL | stock (-> MIXER) | stock |
| MONITOR enc | return 0 (stock -> MIX MASTER) or consume (open question) | same |

Every swallowed key must return R0=1, or the base default yanks to Channel (0x41c350).

### 7.5 Reuse from Roland
Screen object, alloc, canvases, gsave/grest, present path; draw API (section 4.3); fonts 18/13/0; bitmap 0xf5 via 0x34e958; sprintf 0x324010; base-tick meter arrays; preamp/comp param API on 0x43f444 (hardware, observers and settings save stay stock); channel handoff [0x419000]; in-place recreate pattern 0x3107d2; SWITCH/BACK/redraw.

### 7.6 Risks
| risk | mitigation |
|---|---|
| **Brick (rule 1).** | Nothing new runs before 0x31bbf6. The vtable words are written at runtime from ui_hook, so the init data, meminit table and screen table stay byte-identical (rules 5/6). No new pre-scan bytes. |
| A crash in our HOME Draw happens every boot (post-scan). | Still recoverable with {6,1} (combo modes never call arm_stub). Prove every method live over the lab link before flashing. |
| .data writability | Same region as runtime-written globals (e.g. [0x408d64], [0x4094f0]): I writable. Verify with a lab read-back first. |
| Code space: 428 B .text area is too small | Append after the init table like the lab backend (0x3A3000, tools/build_dsp.py, already flashed). Check the size against the ~360 KB free. |
| Object has only 12 own bytes (this+0x884..0x88f) | Keep page state in our vars, not in the object. |
| UI task stack unknown | Small frames; no recursion; sprintf only with short formats. |
| Popup overlay 0x35a1c4 / HMI busy can eat keys over our page | Accept; test SCENE/clock popups. |
| GR/VU semantics | Input meter is post-strip, pre-comp (I). GR only reflects Roland's comp. |
| Gain encoder acceleration | Use our own one-step-per-event in the GAIN block. Never call enc ioctl 2 with -1 (locks it). |
| A-S still reachable via Control Panel (0x2713 -> 19) | Leave stock in v1. |
| Persistence needs a rule-7 exception | v1 without flash; v2 only after an explicit OK. |
| TUBE DSP cost | Measure with the CPU meter before enabling on 8 ch. |

### 7.7 Build plan (each hardware step: one short "go?" line before running it)
1. **Lab, read-only, no flash:** read [0x402418..0x40248c] and [0x401ca8..0x401d18] (expect the stock words above), W[0x419a5e] while navigating, W[0x43ed0c] and bitmap 0x44544c while pressing each button. Proves the key-code -> panel-label map (GAIN_SW = gain push?) and the screen numbers.
2. **Lab, live page:** upload a minimal Draw (Clear + "STRIP CH n") + KeyDown returning 0, write [0x40245c]/[0x402444], press GAIN_SW on Channel. Proves the vtable hijack and .data writability. Power-cycle restores stock.
3. **Lab:** add the VU/GR bars (Tick + 0x3585ec) and the grid; check the meter response against a signal.
4. **Lab:** editor + key map + scrolling list; wire HPF/LOW/PEAK/HIGH to the strip engine and GAIN/COMP to 0x43f444. Check by ear and meter.
5. **Flash build 1:** the same code appended at 0x3A3000+. ui_hook writes the H1 words once when ARMED. Builder checks per FLASH_SAFETY section 6. Test: boot, GAIN_SW -> STRIP, power-off save still says Saved, the {6,1} combo still enters the updater.
6. **HOME (lab first, then flash build 2):** compact meters + idle Hex girl (H3/H6). Test wake on signal, key and encoder; check that a fade does not trigger the girl.
7. **TUBE stage** in strip.c; measure cycles; add the block.
8. **Persistence (v2):** only after an explicit OK of the rule-7 exception (section 5.3). First read the real sector-18 tail over the lab link (0x200F1A78+).

## 8. Open questions
1. Panel labels: is GAIN_SW the GAIN knob push or a printed AUTO-SENS button? What are MIX_SEL and PHONE_SEL, and is MONITOR the main/phones level knob? (build step 1)
2. Sw key index == logical code (affects the combo names): confirm with the stock test mode or lab step 1.
3. Is .data at 0x402418 / RAM table 0x43ae58 writable at runtime (no CPLB write protect)? (step 2)
4. History-stack rules: what BACK from our page returns to; meaning of entry byte 'a'.
5. Who sends 0x2713 (0x32227c, vt 0x403910) and the remote injector messages (Control Panel / MIDI)?
6. Exact args of 0x34ef54 (centred text?) used by SYSTEM Draw 0x31aea0.
7. Blit polarity end to end (0x34ea14 vs 0x34e958) and whether DrawString alone marks rows dirty (0x34aac4).
8. Who sets the L1 meter descriptors, and the channel counts at 96k/192k (4-meter mode when 0x30beba(0x43f85c,0,0)==3).
9. Where the fine DSP trim W[0xff800158+2*ch] is written; SENS units (getRange 0x30987a vs acon -f*16).
10. Does any screen change the 50 ms tick (0x35890e callers 0x31a922, 0x35d290..), stopping the meter copy there?
11. Real sector-18 tail content and NOR part / erase time (persistence v2).
12. Design choices for the user to OK: the 7.4 key map, MONITOR on the page, idle N and threshold, whether factory reset clears the strip record, keep or kill screen 19.
