# Rosetta Dual: two OCTA-CAPTUREs as one ASIO device, no coax cable

Status: **Windows driver v0 runs on the two units** (2026-11-08). Measured facts are marked **M**; the rest is plan.

## Status (v0, Windows)
- `dual/` builds `RosettaDual.dll` (an ASIO driver, MSVC 2022, `dual/build.cmd`), plus `dualhost.exe` (a test host) and
  `test_drift.exe` (core tests).
- **Core tests, all pass:** drift +38 / -120 / +90 ppm at 44.1-96 kHz, with and without +-0.2 ms timestamp jitter.
  Ratio exact to 0.3 ppm, delay wander < 0.3 samples p-p, 96-140 dB clean, no underruns.
- **On both units, 2 min run:** 20 in / 10 out at 44.1 kHz, 256-frame buffer, 172.3 callbacks/s (exact), 0 underruns.
  Unit 2 locks within about 6 s, and both units hold the same buffer depth within 0.3 samples. **M**
- **Input latency is 1424 frames (32 ms).** That is the safety buffer for the way the pairs are merged now; it is
  reported to the DAW, so recordings line up. Lowering it is the first optimisation.
- **Not done yet:** unit 2 outputs, the latency optimisation, an alignment test with one signal into both units
  (cable), channel names from Rosetta Octa Control, and an installer.

## Findings from the cable test (2026-11-08, unit 1 OUT 4 -> unit 2 IN 1, unit 1 OUT 3 -> unit 1 IN 1)
- **The physical clocks of the two units differ by only ~2.4 ppm** (0.1 samples/s). Measured from the audio itself:
  noise played by unit 1 and recorded raw by both units, 85 s. **M**
- **Unit 1's packets follow unit 1's clock.** Timestamps and frame counts are steady (jitter ~20 us). **M**
- **Roland's driver hands over unit 2's packets on its own timer** (about the nominal 44100/s by the PC clock). It
  catches up with an extra packet now and then.
  - Unit 2's packet timing therefore says "-123 ppm" while its audio content runs at unit 1's rate. **M**
  - An "early" packet is a catch-up, not a repeat: dropping it loses real audio. **M**
  - With every frame kept, unit 2's content is continuous. **M**
- **The 3 ms period loses data** (~16,700 discontinuities in 25 s with 10 streams). Default period (10 ms) = clean.
  The render thread must be separate from the capture thread, or outputs starve.
- **Consequence:** unit 2's timing cannot come from its timestamps. The ratio is tiny (ppm), but the driver's pacing
  hides where unit 2's content is in time, by up to one packet (10 ms). Next design:
  - Follow unit 2 by its long-term frame count (fill averaged over the catch-up cycle).
  - Start from the ratio remembered from the last session.
  - Optional: a calibration with a loop cable to pin the offset.

## Second round (2026-11-08 evening)
- **Catch-up model (paced mode):** perfect in simulation (`test_paced`: ratio exact, 0.03 samples wander). On the
  hardware, Roland's pacing changes from run to run: one run has catch-ups every ~80 s on unit 2; another 300 s run
  has both units paced with a 10 ms sawtooth and no catch-ups. So it is off by default (`RD_PACED=1`).
- **Over minutes the frame counts are honest:** a 300 s run gives unit2/unit1 = +3.9 ppm, against +2.4 ppm measured
  from the sound.
- **Level mode (default now):** unit 1 is read 1:1; unit 2 runs at the remembered ratio and is nudged by its buffer
  level averaged over minutes.
  - Clean audio both sides, no dropouts.
  - Alignment still slid 1.4 samples/s in the test, because of a wrong remembered ratio saved by an earlier
    paced-mode run.
- **Open:** even unit 1's own output -> input loop slid ~1.3 samples/s against the noise sent. So Roland's WDM
  exclusive path itself is not sample-exact between streams. Sample-locked two-unit recording without a reference
  signal is not possible on this driver; a loose sync (a few ms, no slow drift) is.

## Third round: our own USB path (WinUSB, 2026-11-08)
Both units on WinUSB (Zadig; serials ISRL89F2C5C5, ISRL91AA2FC4). Tools: `dual/build_usb.cmd`, `usbprobe`, `usbdual`.
- Protocol as in Linux `sound/usb/quirks-table.h` / `quirks.c` (0582:0120), confirmed by the descriptors **M**:
  - iface 0 playback ep 0x05 (iso async, 10 ch), iface 1 capture ep 0x85 (iso async, implicit feedback, 12 ch), S24_3LE;
    alt 1/2/3/4 = 44.1/48/96/192 kHz (4 ch at 192k); iface 2 MIDI (06/86), iface 3-4 interrupt.
  - Rate: vendor request 3, read wValue 1 = rate (24-bit LE) + status; write wValue 8 = `40` + rate.
- Both captures streamed together, 480,000 microframes, **0 errors**, each within +-1 sample of a straight line. **M**
- **The "-123 ppm" was the USB controllers, not the boxes.** Controller of unit 1 runs +121.9 ppm vs the PC clock,
  controller of unit 2 -1.1 ppm. Corrected to PC time the boxes differ by **-2.0 ppm**, which agrees with the 2.4 ppm
  measured from the audio. **M**
- So sample counts per USB tick + each controller's frame clock (WinUsb_GetCurrentFrameNumber, 8-23 us residual) give the
  exact relation between the boxes. Next: playback (implicit feedback), then the engine under the ASIO driver.

## Linux and macOS
- **The core (`dual/src/core`) is plain C++17 with no OS code.** The Linux/macOS front ends reuse it.
- **Linux (plan):** PipeWire already does what Rosetta Dual does: it resamples a follower device onto the driver's
  clock. With both units on separate controllers, a PipeWire combine-stream config (or JACK + `zita-a2j`) gives one
  20-in device.
  - Rosetta Dual for Linux = that config, plus a check script. Our core goes into a small JACK/ALSA client only if
    PipeWire's result isn't good enough.
  - Check first: does the OCTA-CAPTURE run on `snd-usb-audio` (Roland vendor quirk)? Inferred, not yet tested.
- **macOS (plan):** Audio MIDI Setup -> aggregate device, with "Drift Correction" ticked on unit 2. That's built in, no
  code.
  - It needs Roland's macOS driver for your macOS version (Apple Silicon support not verified).
  - Roland's own manual already describes the aggregate route for VS EXPAND.


## Why
Roland's two-unit mode (VS EXPAND) needs a coax cable for the clock, and that cable takes unit 1's digital input.
You get 18 usable inputs. Without VS EXPAND, Windows sees two separate units (**M**, 2026-11-08), so a driver of our own
can join them.

## Measured (tools/dual_drift.py)
- With VS EXPAND off on both units, Windows exposes unit 2 as `3- OCTA-CAPTURE`, with its own endpoints:
  1-2 ... 9-10 + MAIN in, 1-2 ... 9-10 out. **M**
- Both units on the **same USB controller**: streaming both at once starves them (about 12 k samples/s combined).
  On **separate controllers**, both run at the full 44.1 kHz at the same time. **M**
- Clock drift unit 2 vs unit 1: **+38 ppm**, which is 1.7 samples/s, or 2.3 ms per minute (60 s least-squares fit). **M**

## How it works
- **An ASIO driver is a user-mode COM DLL, not a kernel driver,** so there is no driver signing. Install = register the DLL
  (one HKLM\SOFTWARE\ASIO key + CLSID).
- **Backends:** WASAPI exclusive, event-driven, one stream per stereo endpoint (Roland's WDM exposes pairs).
  - Unit 1 is the master clock. The ASIO buffer switch is driven by unit 1's capture events.
  - The pairs of one unit share that unit's clock: align them once at start, then they stay locked.
- **Drift correction for unit 2:** a ring buffer per unit-2 stream, and a PI controller on the buffer fill level.
  The controller steers the resampling ratio around 1 ± 100 ppm. The resampler is windowed-sinc polyphase, transparent
  (stopband < -120 dB), and the ratio is smoothed so pitch never wobbles audibly.
- **Channels:** 20 in (unit 1: 1-10, unit 2: 11-20) and 20 out. Names in the ASIO channel list come from the
  Rosetta Octa Control input names.
- **Rates:** whatever both units are set to: 44.1 / 48 / 88.2 / 96 kHz. 192 kHz is the OCTA's 4-in/4-out mode, which
  becomes 8 in total.
- **Latency:** unit 1 as native as WASAPI exclusive allows. Unit 2 + the resampler add a small fixed delay, which the
  driver reports to the DAW so recordings line up.
- **Limits:**
  - Unit 2 is matched to a fraction of a sample, not sample-locked. That's fine for separate sources, but not ideal for
    one source mic'd across both units.
  - Each unit must be on its own USB controller (see Measured).

## Licence
- **The ASIO SDK has been dual-licensed by Steinberg, proprietary or GPLv3, since 2025-10.** Rosetta Dual is built on
  the GPLv3 option, so it is free to distribute as open source.
- It lives in `dual/` with its own GPLv3 `LICENSE`. The rest of this repo stays MIT.
- The SDK itself is not committed; the build fetches it.
- There is no Roland code: it talks to the units through Windows' normal audio APIs.

## Plan
1. Python prototype: open all pairs of both units at once (WASAPI exclusive), check stability, run the drift loop,
   and write a 20-channel WAV. Prove the alignment with a click played through both units.
2. C++ ASIO DLL (MSVC 2022, present on the main PC): backends + resampler + PI loop; test in Cubase.
3. Installer (register/unregister), README, release.
