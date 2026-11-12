# Mic compensation

**TL;DR:** give the app a microphone's frequency response and it sets the input's EQ to correct it, towards flat or towards another mic. Tested off-hardware only so far.

## Using it

The **MIC** button in Rosetta Octa Control steps through the curves in `app/mics/` for the shown input. A dashed line on the EQ screen shows what the mic's curve asks for, the solid line shows what the EQ does, and the status line says how much colour was fitted and how much is left. Click through to **MIC OFF** and the EQ goes back to how it was.

To match another mic instead of flat: `mic 1 1 e935 othermic` in `app/do.txt`.

## Adding a mic

Put a file in `app/mics/`, named after the mic, with one `Hz, dB` pair per line:

```
# my mic, read from its manual
100, -3
1000, 0
5000, 4
```

At least 6 points, and the level doesn't matter. If the manufacturer's PDF has a vector graph, the numbers can be read straight out of it, which is how `e935.csv` was made (Sennheiser quick guide 50161, 08/2024).

## What it does and doesn't do

- It uses the strip's existing low shelf, peak and high shelf, so it costs no extra DSP. The HPF, tube and comp stay as they are.
- It corrects 120 Hz to 11 kHz only. A mic's natural roll-offs are left alone, because boosting them mostly adds rumble and hiss.
- Boosts stop at +4 dB, cuts at -9 dB. Cutting is cheap, boosting costs headroom.
- e935 towards flat: 2.6 dB rms of colour goes down to 0.5 dB left, worst point 1.7 dB. This is the calculation, not a measurement of the mic.
- Published graphs are on-axis averages without proximity effect, and individual mics differ. Treat it as tone shaping, not exact correction.

Files: `app/mic_comp.py`, `app/tests/test_mic_comp.py`.
