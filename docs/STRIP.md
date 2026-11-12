# Channel strip (Rosetta, OCTA-CAPTURE)

TL;DR: a biquad engine runs on inputs 1-8 before Roland's own chain, so the monitor mix and the recording both
hear it. Each input has 4 stages (HPF, low shelf, peak, high shelf), all off at boot (= stock sound). Set them
live from the PC over the lab link (`tools/octa_lab.py strip ...`); no reflash per tweak.

## Signal order
`strip (HPF -> low shelf -> peak -> high shelf) -> Roland trim -> polarity -> stock low-cut -> stock comp -> mix/record`

## Commands (lab >= 1.3)
| command | does |
|---|---|
| `strip` | status: stages on per input, output clip count |
| `strip hpf CH FREQ [Q]` | high-pass, 12 dB/oct (Q 0.707 default) |
| `strip low CH FREQ DB [Q]` / `strip high ...` | low / high shelf |
| `strip peak CH FREQ DB [Q]` | peak/dip |
| `strip off CH [stage]` / `strip off all` | stage or channel off |
| `strip selftest` | checks the Blackfin multiply against Python (needs patches/lab1.nm of the flashed build) |

The sample rate comes from the meter period (CCLK 372.4 MHz); `--fs N` overrides. The strip runs at
44.1/48/88.2 kHz only (the rates that use Roland's 48k chain); 96k/192k are stock for now.

## How it works (patches/strip.c, patches/lab1.S wrap48)
- wrap48 (IVG10, once per sample frame) calls `strip_pre(in)` then Roland's chain 0xffa08054.
- Frames are Q27 (full scale = 2^27). Coefficients Q30, stored b0 b1 b2 -a1 -a2, exact 32x32 multiply in 7
  MAC instructions + error feedback (the dropped fraction is carried to the next sample: no DC offset, no
  low-frequency noise build-up). Matches a float reference to ~-138 dBFS (1.2 LSB of 24-bit).
- Headroom: each stage is held to +-8 FS (+18 dB), the strip output to +-4 FS (+12 dB, Roland's frames have
  4 bits of headroom); output clips are counted. Saturating adds: overload clips, it never wraps.
- Live changes: the PC writes the idle coefficient set of a stage and flips its `sel` word (one store), so the
  ISR never sees a half-written set. Enabling a stage zeroes its state first.
- Block in memory: address from PING (lab >= 1.3), magic 'STRP', layout checked by the PC before writing.

## Amp mode
One guitar amp per box replaces tube and comp on one input (strip v3): see [AMP.md](AMP.md).

## FAT and SLIM (built 2026-11-10, not yet on a unit)
Both run the strip from the chip's fast instruction memory (`patches/fast.S`, loaded by memory DMA after boot), where today it runs from SDRAM. The **FAT/SLIM** button picks per box:

| | FAT | SLIM |
|---|---|---|
| Filters | full precision | peak and high shelf use 14 multiplies instead of 20, only where the response moves less than 0.05 dB; low bands always stay full |
| Comp | detector every sample | detector every 8 samples (staggered across inputs), gain applied every sample |
| Sound | reference | the same EQ; on steady tones the comp works 0.1 dB (bass) to 0.65 dB (5 kHz) harder, because it sees true peaks |

**Measured on unit 1 (2026-11-10, `profile 1`, cycles per input):**

| block | normal (SDRAM) | FAT (fast memory) | SLIM (fast memory) |
|---|---|---|---|
| HPF | 131 | 172 | 161 |
| peak | 133 | 176 | 153 |
| tube | 191 | 214 | 208 |
| comp | 282 | 319 | 190 |

Fast memory made it slower, not faster: the instruction cache already held the code. The SLIM comp is the real saving.

**The overhead, found (same day, cycle counters inside the audio interrupt, `probe 1 inside` / `probe 1 micro`):** loads and plain instructions cost 1 cycle; a taken branch 6-10 and a call ~10. The strip was branches and calls: 8 empty inputs cost 366 cycles a sample to skip. Fixes, all bit-exact on the box: a bitmask scan of active inputs (366 -> 114 with nothing on), a filter with its loads overlapped and the second multiplier used (79 -> 59 cycles), and a straight path when all six blocks are on. A full input went from 1,003 to 677 cycles (FAT) and 550 (SLIM). Inlining the filter was tried and measured ~20 cycles slower.

The tube's DC blocker uses a 3-tap filter in both, bit-exact with the full one. Measure the gain on a unit with `bench <unit>` in `app/do.txt`: it self-tests the code against the maths model first, then ramps inputs and stops at 80 % load.

## Next
- Measure the cost per stage on hardware (meter), then move the hot loop to free L1 code if needed.
- Gate and compressor stages, presets (Vocal, Speech, Kick, ...), a Rosetta menu page per channel.
