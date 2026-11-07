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
- **"Solo channel" mode** (see below): the user says "that's what we gotta implement".
- **Replace AUTO-SENS** (stock auto-level is useless for rock monitoring). Idea: **gain-compensated clip guard**. When the input nears clipping, step the analog preamp gain down and add the same amount back digitally right after the ADC, so the monitor and record level stay put and only the headroom changes. Needs the digital step time-aligned with the ADC latency (a few samples) to avoid clicks. Fallback: reuse that menu slot for our own pages.
- The user has a second never-used menu feature; find out which one. Its slot can be reclaimed too.

## Later
- A delay per channel. Costs memory, not CPU: about 384 KB per second at 96 kHz. Find the RAM size first.
- 1-2 shared reverbs on sends.
- **"Solo channel" mode:** fewer active channels means more CPU per channel. Example: a 1-2 channel mode with a fat chain (multiband comp, de-esser, better reverb, delay) instead of 8 basic strips. Fixed modes picked from the menu, never reallocated while audio runs. 48 kHz gives about twice the budget of 96 kHz.
- A way into update mode without buttons (USB/MIDI command), so a dead key never locks a unit out.
- Animated JSW OS / ROSETTA boot.
