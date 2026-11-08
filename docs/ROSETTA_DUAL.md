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
