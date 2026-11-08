# POWER: soft power button, power-hold latch, auto power-on

TL;DR
- Firmware-only auto power-on is **not possible**. While "off" no code runs: the CPU is unpowered (or held in
  reset) until the button is pressed. The button switches the board ON in hardware; firmware then holds power with
  **PH13** and switches it OFF by dropping PH13. INFERRED, strong (reasons below).
- Button = **PH15** (GPIO 47), active HIGH, polled every 10 ms, 1.5 s hold = power-off. Power hold = **PH13**
  (GPIO 45), HIGH = stay on. CONFIRMED in code.
- Way to get auto-on: hardware. Put a short (about 0.3 s) "button press" across the power switch when DC (or USB
  VBUS) comes up. No firmware change is needed, and button power-off plus the settings save keep working.

Addresses: main .text addresses are runtime addresses (same as main.dis). Data addresses are runtime addresses,
mapped with build_splash_main.parse()/run(). L1 code is given as runtime address plus its main.dis file address.

## 1. GPIO pin numbering (CONFIRMED)
gpio driver vtable 0x43c4f8 (device table entry "gpio"). Name lookup 0x368f84 returns [vtable+8] = fn table 0x43c504.
Pin number = port*16 + bit, where port 0=F (0xffc007xx), 1=G (0xffc015xx), 2=H (0xffc017xx).

| fn | addr | action |
|---|---|---|
| +0x00 | 0x3628f6 | output: DIR set, INEN clr, FER clr |
| +0x04 | 0x362838 | input: DIR clr, INEN set, FER clr |
| +0x08 | 0x3627e6 | drive HIGH (PORTxIO_SET) |
| +0x0c | 0x362794 | drive LOW (PORTxIO_CLEAR, PH = 0xffc01704) |
| +0x10 | 0x362742 | toggle |
| +0x14 | 0x3626e0 | read PORTxIO bit, returns 0/1 |

## 2. dev_power driver (CONFIRMED unless tagged)
- Device table entry 0x41c6a4: name "power" 0x41c4bc, vtable 0x419fa4, cfg 0x408e68.
- cfg 0x408e68 = { "gpio", 1500, 47, 45, 42 } = {driver, hold ms, button pin, hold pin, aux pin}.
- State block 0x448928 (bss).

| pin | GPIO | dir | level | role |
|---|---|---|---|---|
| PH15 | 47 | in | HIGH = pressed | power button sense |
| PH13 | 45 | out | HIGH = power held | power-hold latch (same pin the loader drives first) |
| PH10 | 42 | out | driven LOW at open | unknown. ioctl 2 can drive it; no stock caller found |

- open 0x36843c: input(47); output(45); output(42); SET PH13; CLEAR PH10 (done twice). Arms the auto-off timer
  0x36836c(100000 min, no cb). Creates the cyclic handler 0x3683b0, period 10 (INFERRED: ms), via 0x3669d4.
- Cyclic 0x3683b0 (button poll):
  - Reads PH15 (fn +0x14).
  - LOW: sets flag **[0x419fa0]=1** ("seen released") and clears the counter [0x448944].
  - HIGH: counts only if [0x419fa0]!=0, adding 10 per tick. At >= 1500 it latches [0x44892c]=1 and calls the
    callback [0x448928]. It fires once.
  - [0x419fa0] is 0 at boot (init data), so **the press that powered the unit on is ignored until it is released**.
  - It then calls 0x3682d8, the auto-off minute countdown.
- ioctl 0x3684e4, table 0x43d9e8:

| cmd | code | effect |
|---|---|---|
| 0 | 0x368508 | set button callback, reset latch/counter, start the cyclic |
| 1 | 0x36852c | stop the cyclic |
| 2 | 0x368534 | PH10 = arg (SET/CLEAR) |
| **3** | **0x368550** | **CLEAR PH13 = power off** |
| 4 | 0x36855a | auto-off enable(arg) (0x368388 / 0x36835c) |
| 5 | 0x36856e | auto-off minutes + callback (0x36836c) |
| 6 | 0x368578 | auto-off "activity" reload ([0x448960]=1) |

- PH15 is also **sw key 8** ("sw" cfg 0x408e3c: 8 extio keys, then gpio pins {47,46} = keys 8,9; open 0x368760).
  The power-on combo scan 0x31bc0c reads keys 0..8. The combo tables 0x435d50-0x435da0 use only keys 0..6, so the
  power button state has no effect on the combo (CONFIRMED). PH14 = key 9, an unknown button.

## 3. Consumers (state machine)
| site | what |
|---|---|
| 0x3197ca-0x319804 | boot worker 0x319740, modes other than 1/2 (normal): ioctl0 cb **0x31964c**; ioctl5 240 min, cb 0x319660 (auto-off warnings/expiry) |
| 0x31964c | button held 1.5 s: posts event **0x2712** |
| 0x30d890 | event 0x2712 -> 0x358678(6) = screen 6 (table 0x43a980[6] = obj 0x435808, vtable 0x402548) |
| 0x30d4cc | system param 15 (AUTO OFF "OFF"/"4HOURS") -> ioctl 4 |
| 0x302834 / 0x30295a | UI activity -> ioctl 6 (reload auto-off) |
| 0x35b288 -> 0x35b2b0 | alternate cb **0x35afc0**: ioctl **3** at once (PH13 low, no save), then screen 22. Registered by boot-screen init 0x31983c/44 when B[0x43f90d]==1, and by 0x356546. Which cb is live in a given mode: INFERRED, the last ioctl 0 wins |

## 4. Power-off sequence, normal path (CONFIRMED code; job dispatch INFERRED)
1. Hold PH15 for 1.5 s (only after it has been seen released): 0x31964c, event 0x2712, screen 6.
2. Screen 6 init 0x3192e4 queues worker **0x31929c**: 0x30ec2c(6), 0x31d058, **0x31d160 save settings** (sector 18),
   then 0x31dae0(1,0).
3. Draw 0x319368: "POWER OFF" + "Saving Parameters", then "Save Complete" + "Goodby".
4. 0x31dae0(1,0) completes job 1 in table 0x419148. Job 1 = **0x31db4c** (pointer at 0x41915c; the dispatch mechanism is INFERRED):
   - event 5;
   - 0x356768;
   - 0x36da08;
   - open "audio_serial", ioctl 1 (INFERRED: audio stop);
   - **dly_tsk(1000)** at 0x31db98;
   - open "power" (0x436180), **ioctl 3 at 0x31dbaa: PH13 LOW** (W[0xffc01704]=0x2000 via 0x362794);
   - close, return.
5. Nothing follows: no loop, no IDLE/hibernate, no VR_CTL (0xffc00008) write anywhere in main. The only PLL writes
   (0x32aa16/0x32aa22) are boot-time PLL setup from the kernel start. CONFIRMED (no exact .H/.L pair for 0xffc00008).
- Other PH13 writes in main:
  - 0x3298dc-0x3298ee: the reboot-to-loader peripheral reset 0x329724, reached only from 0x32a8fe. It sets
    PORTHIO_DIR=0 and PORTHIO_CLEAR=0xFFFF, so PH13 floats/goes low for the brief time until the loader re-drives it.
    CONFIRMED. The unit survives this, which suggests the hold node has some hold-up or pull: INFERRED.
  - 0x302e18: makes **PH9** an input (called from 0x31d740), role unknown. Not PH13.

## 5. Main after boot / standby (CONFIRMED)
- Booter and loader read no buttons. The loader drives PH13 HIGH as its first I/O (0x200015a4-0x200015da), then
  LCD, sum check, unpack, jump. See notes_safety_bootflow.md.
- Main goes straight to normal operation. Boot worker 0x319740: param load, dly 1 s, event 0. There is **no standby
  state, no "wait for power key" loop, no power-on flag**. ' POWER "ON"/"OFF"' (0x43bf40) is the PHANTOM test-mode
  text, not system power.
- Nothing runs while "off", and there is no wake path in main.

## 6. USB / VBUS (CONFIRMED regs; meaning of bits INFERRED from BF52x map)
- USB init 0x364614: clears/sets INTR regs and ends with USB_POWER=0x60 (HS_ENABLE|SOFT_CONN). It connects
  unconditionally.
- The USB ISR at L1 0xffa02212 (main.dis 0x39e00c) reads USB_OTG_DEV_CTL **0xffc03900** and compares it to 0x99
  (VBUS valid + session) on INTRUSB bit 0 (SUSPEND). It also handles bit 5 (DISCON), bit 2 (RESET) and bit 1 (RESUME).
- So VBUS / "PC present" is visible to firmware **only while the unit is on**. It cannot wake the unit.
- USB_POWER=0x20 at 0x3297d2 is in the reboot reset routine: it drops SOFT_CONN before jumping to the loader.

## 7. Verdict
**(a) Firmware-only auto power-on: NO. No patch exists.**
Reasons (INFERRED from CONFIRMED pieces):
1. The cold-boot path booter -> loader -> main has no button check and no standby (sections 5 and 2). If the CPU
   ran on DC alone, the unit would already start by itself. It does not, so the CPU does not run until the button.
2. The loader asserts PH13 as its very first action. That is the "grab the latch before the user lets go" pattern.
3. Power-off = PH13 low, then return. There is no sleep loop and no wake handler, so off is not a CPU state.
4. Button arming [0x419fa0] ignores the press that is still held at boot, so firmware expects to start mid-press.
5. A hung main makes the button useless and only DC pull helps: the button can turn power ON in hardware, but OFF
   needs firmware.

**(b) Hardware latch, seen from the firmware:**
- PH13 (GPIO 45) = hold output. HIGH from the loader onward. LOW = request power off.
- PH15 (GPIO 47) = button sense. HIGH while pressed. The button also closes the power path by itself (INFERRED).
- Likely circuit (INFERRED): regulator enable = (button) OR (PH13 through a transistor/diode). PH15 senses the
  button node through a divider/diode.
- Checks to confirm on the board:
  - unit off, DC in: CPU I/O rail ~0 V;
  - unit on: PH13 ~3.3 V;
  - PH15 0 V released, 3.3 V pressed.

Hardware bypass options:
| option | how | firmware effect |
|---|---|---|
| **A, recommended** | momentary closure across the power switch, ~0.2-0.5 s, triggered by DC rising (RC + transistor/opto) | none. Loader latches PH13, button off + save work. Key 8 pressed during the combo scan is harmless (not in any combo) |
| B, "PC on" | same pulse driven from USB VBUS 5 V through an opto (keeps PC ground off the switch node) | a pulse while already on is shorter than 1.5 s and is ignored, so no accidental off |
| C, not recommended | permanent bridge across the switch | PH15 stays HIGH, [0x419fa0] never arms, so **button power-off is dead and the settings save at power-off (0x31d160) never runs**: changes since the last save are lost when DC is pulled (other save site 0x31e3a4: trigger not traced). sw key 8 stays pressed (UI effect unknown) |

- Do not put a strong pull-down on PH13 (see the reboot-path float in section 4).
- Future note (not done): firmware auto-OFF when VBUS drops (PC shutdown). Hook the USB ISR suspend/DISCON path
  (0xffa02212, OTG_DEV_CTL!=0x99) to post event 0x2712, which runs the normal save + PH13 drop. The code must run
  only after the combo scan 0x31bbf6-0x31bd46.
