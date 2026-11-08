# Guitar amp mode

**TL;DR:** a guitar amp that runs inside the OCTA's own DSP, so you hear it with no PC delay and it's what gets recorded. Built and passing its host tests. **It hasn't run on hardware yet** (test build `strip7`).

## How it works

It replaces the tube and comp on one input, and the strip's EQ becomes the amp's tone controls:

```
input -> HPF -> top cut -> GAIN STAGE 1 -> low cut -> GAIN STAGE 2 -> LOW / PEAK / HIGH -> SPEAKER -> level
                                                                       (tone stack)
```

- **Gain stages:** the strip's tube curve (a cubic soft clip with a bias offset, so it makes even harmonics as well as odd), run twice for more gain.
- **Between the stages:** the low cut (80-170 Hz) keeps high-gain voices tight instead of farting out. The top cut before stage 1 keeps the fizz down.
- **Speaker:** a resonant high-pass around 100 Hz, a presence peak at 2.6 kHz and two low-passes (5.2 and 7 kHz). These are filters, not a measured cab impulse.

| Voice | Gain | Low cut | Character |
|---|---|---|---|
| CLEAN | 12 dB | 80 Hz | breaks up only when you dig in |
| CRUNCH | 26 dB | 120 Hz | classic overdrive |
| LEAD | 40 dB | 170 Hz | sustain, tight lows |
| CRANKED | 24 dB, one stage | 80 Hz | the strip's tube at full drive, bias 0.3, plus the speaker |

## Using it

The **AMP** button in Rosetta Octa Control cycles the voices for the shown input (or `amp 1 1 crunch` in `app/do.txt`). There's one amp per box. Like the rest of the strip it's off at boot and its settings are RAM-only for now.

## Limits

- **CPU:** Roland's own code uses about 39 % and a full strip about 12-14 %. The amp is 6 biquads plus 2 gain stages, an estimated 17 %. Not measured on the unit yet, and that's the point of the first flash.
- **No oversampling,** so the highest-gain voices alias somewhat. The top cut before the gain stages is there to limit it.
- A real cab impulse probably won't fit next to everything else. Untested.

Files: `patches/strip.c` (`amp_gain`, `amp_speaker`), `tools/octa_lab.py` (`Strip.amp`, `AMP_VOICES`), `patches/tests/test_strip_host.py`.
