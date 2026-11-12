# OCTA-CAPTURE Rosetta (JSW OS): roadmap

## Done
- [x] Unpack, repack and checksum tool (`octa_fw.py`)
- [x] Boot splash: the Rosetta peek (v4). **Works on hardware** (2026-11-07, second unit)

## Next
0. **Lab backend (lab1):** peek/poke/run code over CTRL USB-MIDI (docs/LAB.md). Built and host-tested; flash with test/lab1. Then memprobe/memcheck to prove the SDRAM size and the free areas.
1. **CPU-load meter.** Find the audio interrupt (the SPORT DMA handler) and time it, then show the load % on the LCD. This sets the budget for everything below.
2. **Find the insert point.** Locate the stock per-channel compressor in the Blackfin's audio loop. The user confirmed the comp IS recorded (it runs before the USB send), so the strip goes right next to it.
3. **First DSP test: a high-pass filter on one channel.** Smallest possible audio change; prove we can touch the signal safely.

## The channel strip (the user's goal: plug a mic in and it sounds good)
Per mic channel, all 8, each with its own settings:

`preamp gain -> HPF -> gate -> 4-band EQ -> comp -> output trim`

- **Gain:** the analog preamp is already digitally controlled (AUTO-SENS). Reuse it.
- **HPF:** 12 or 24 dB/oct, about 20-300 Hz. Kills rumble and plosives.
- **Gate/expander:** optional, cheap.
- **EQ:** low shelf, 2 parametric mids, high shelf. About 10 cycles/sample per band.
- **Comp:** the stock one, or our own with threshold, ratio, attack, release and makeup.
- **Presets:** Vocal, Speech, Kick, Snare, Guitar amp, DI bass, Flat.
- **UI:** a Rosetta scrolling-menu page per channel (never squeeze parameters).

## Wanted (user, 2026-11-07)
- **Auto power-on: PARKED 2026-11-07, the user doesn't want a hardware mod.** Firmware "standby, wake on PC USB" also ruled out: the user switches the mains off with the PC, so every start is a cold DC start (only a power-up pulse across the button can do it: opto + RC, ~3 through-hole parts). Simplest: a wire across the button pads (always on with DC; the press held from boot is ignored) + a firmware auto-save after changes. User: "probably not worth dealing with" (2026-11-07). Better variant (user): wire DC+ (~10k) to the regulator-EN side of the button/PH13 diode OR, so the button still reaches PH15 -> repurpose it as QUICK RESET (save + reboot via 0x329724) + a hardware WATCHDOG petted from the UI tick (~8 s timeout, covers hard freezes; must survive a sector erase). Needs board photos of the power-button area first. Firmware-only, no wire: SHORT TAP of the power button (< 1 s, poll PH15 from ui_hook) = save + clean restart, fixes the Cubase driver hiccups that today need a power cycle (check the reboot param so it boots main, not the updater). User: later. Auto power-OFF is not wanted (turning off is fine; only turning on is the pain). Details: **hardware only** (re/POWER.md). While "off" the board is unpowered; the button turns it on in hardware and firmware holds PH13 high (power latch), button = PH15. Plan: (1) multimeter check across the power switch with DC in, unit off; (2) a short pulse (~0.3 s) across the switch on USB VBUS rising (PC boots) through an opto, through-hole; (3) firmware add-on: on VBUS loss (PC shut down) post event 0x2712 = the normal save + power-off, so the unit follows the PC both ways. Never a permanent bridge (power-off and the settings save would break).
- **"Solo channel" mode** (see below): the user says "that's what we gotta implement".
- **Replace AUTO-SENS** (stock auto-level is useless for rock monitoring). Idea: **gain-compensated clip guard**. When the input nears clipping, step the analog preamp gain down and add the same amount back digitally right after the ADC, so the monitor and record level stay put and only the headroom changes. Needs the digital step time-aligned with the ADC latency (a few samples) to avoid clicks. Fallback: reuse that menu slot for our own pages.
- The user has a second never-used menu feature; find out which one. Its slot can be reclaimed too.

## Ideas (user, 2026-11-07, after the strip worked on hw)
- **Sonic profiles / console emulation ("SSL console"):** a profile = a whole-chain preset (EQ curves, comp
  character, saturation flavour/drive) loaded from a file. Console flavours = fixed EQ shapes + comp ballistics +
  tube/transformer curves. Could also import a measured curve (fit biquads to a target response on the PC).
  Needs the strip blocks first (gain, tube, comp: strip2), then a profile file format in octa_lab.py, then the
  on-unit menu.

## STRIP page follow-ups (user, 2026-11-07, after the live page worked)
- **Clip finder:** per input, show WHERE it clips: PRE (ADC/preamp too hot -> turn GAIN down), STRIP (a boost or
  the tube pushing past +12 dB), OUT/REC (mix or USB record). DSP side: per-channel counters at the three points
  (in[] before the strip, strip limiter, rec[] after Roland's chain). Later: which band was hot at the clip moment
  (low/mid/high energy) -> "lows clipping".
- **Wave analysis** on the page: scope of the input, or a coarse spectrum (8-16 bands from a filter bank).
- **Rosetta-style Channel (gain) page:** own Draw for screen 2 (same vtable trick), "GAIN" instead of "SENS"
  (label already renamed live: string 0x41c7a0; bake into the flash build).
- **Hi-Z in a menu (user, 2026-11-08):** the knob Channel page (strip6) has no Hi-Z. The user rarely goes direct, so it
  belongs in a menu somewhere, not on the page. Do this before unit 1 moves from strip5 to strip6. Not urgent.

## Ideas (user, 2026-11-08)
- **Guitar amp section:** an input mode that replaces the strip's tube/comp with preamp stages, a tone stack and a speaker curve, in the firmware so it monitors with no PC delay. Speaker as a few biquads; a real impulse response needs a CPU check first.
- **Mic compensation mode:** built (docs/MIC_COMP.md), uses the strip's three EQ bands, e935 curve included. Not tried on the boxes yet. More bands than that needs firmware. Add the user's other mics.

## Slimmer DSP (user, 2026-11-10)
Measured today: about 200 cycles per biquad, a full chain 12-14 % per input. Keep the total under about 70 % (SNEN overrun note in re/AUDIO_PATH.md), which is about 13 bands or 2 full chains. Order, biggest win first; all numbers after step 0 are estimates until the meter runs:
0. **Free test, no flash:** with the lab meter, switch Roland's reverb and stock comps off and see whether their 39 % drops.
1. **Move `_bq_run` (and the strip loop) into free L1 code** (0xFFA048BE.., ~14 KB, inferred free). It is about 45 instructions but lives in SDRAM, so the cost should be cache misses: expect about 50-60 cycles instead of 200. Copy post-scan via MDMA/ITEST (core can't write L1 code, and normal loads from it fault), so the update combo still works if it goes wrong.
2. **One-pole DC blocker** instead of the tube's fifth biquad, about 20 cycles.
3. **Slim band for peak and high shelf:** 32x16 feed-forward taps (14 multiplies instead of 20). Keep full 32x32 for HPF and low shelf, where poles near 1 need it. Bit-exact tests have to be redone for the slim path.
4. Target: about 50 bands, or 6-7 full chains under 70 %. All 8 needs step 0 to free something.
Status 2026-11-10: steps 1-3 built as FAT/SLIM modes (docs/STRIP.md), plus the SLIM comp (detector every 8 samples). Host-tested only. Next: `bench` on a unit, then build it into the firmware (arm_stub loads it) instead of the live upload.

## Ideas (user, 2026-11-11)
- **Scarlett clock that never goes dead:** the Scarlett is slaved to unit 1's coax (S/PDIF). When unit 1 is off or updating it has no clock and mutes everything, front preamps included. The Focusrite keeper (app/focusrite.py) should switch it to Internal when S/PDIF is missing and back to S/PDIF when unit 1's coax returns. Not while another session is using the Focusrite.
- Hex girl in Octa Control as a chibi character the user draws (not the reused desktop/README icon). Left out of the amp head until then.
- Amp head (app/amp_panel.py): modern OCTA-look head done; hear it on hw with the Les Paul.
- **Amp library, all in the program (user, 2026-11-11):** one folder per amp under app/amps/: DSP settings (the 6 filters, 2 clip stages: gain split, bias, soft/hard; the tone stack's frequencies; knob ranges) + its own look (colours, faceplate, knobs, optional art the user draws or makes in ComfyUI). Saved tones (name + knob positions) per amp, as many as wanted. Anything inside the firmware's amp shape is app-only, no flash. A new shape (3rd stage, cab IR, gate inside the amp, 2 amps a box) = firmware. Build after the amp + FUZZ have been heard on hw.

## Later
- A delay per channel. Costs memory, not CPU: about 384 KB per second at 96 kHz. Find the RAM size first.
- 1-2 shared reverbs on sends.
- **"Solo channel" mode:** fewer active channels means more CPU per channel. Example: a 1-2 channel mode with a fat chain (multiband comp, de-esser, better reverb, delay) instead of 8 basic strips. Fixed modes picked from the menu, never reallocated while audio runs. 48 kHz gives about twice the budget of 96 kHz.
- A way into update mode without buttons (USB/MIDI command), so a dead key never locks a unit out.
- Animated JSW OS / ROSETTA boot.
