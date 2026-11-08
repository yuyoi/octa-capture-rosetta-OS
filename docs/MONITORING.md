# Monitoring with two OCTA-CAPTUREs

**TL;DR:** two boxes on USB with no cable between them can't share audio without going through the PC, and that costs delay. For playing live, monitor inside the box, or mix both boxes externally. USB monitoring is there as an option for things you don't play along to.

## The options

| Way | Delay | Uses up | Good for |
|---|---|---|---|
| **Everything you need to hear on one box**, using that box's own monitor | none from the PC | nothing | Guitar and vocal together. The other box records what you don't need in your ears |
| **External mix** of both boxes (below) | converters only, about 1-2 ms (estimate) | a mixer or second interface | One monitor out for both boxes, all 20 inputs still free |
| **MONITOR OTHER BOX** in the app (USB, through the PC) | about 15 ms (calculated, not measured) | nothing | Synths, playback. Not for playing in time |

## The external mix that works (tested)

Scarlett 6i6 2nd Gen as the monitor mixer:

```
unit 1 COAXIAL OUT (9/10) ──> Scarlett S/PDIF IN
unit 2 OUT 1, 2           ──> Scarlett LINE INPUT 3, 4
                              Scarlett mixer ──> monitor outs
```

- The Scarlett's clock goes on S/PDIF at the OCTA's sample rate. It can only lock to one digital source, so **one box goes digital and the other analog**. Two digital boxes would need a clock link between them.
- The OCTA's coax out is OUT 9-10. Set its source to the direct mix: `00 03 00 04` = `00`.
- If the rear outs are silent but headphones work, **check the OCTA's front monitor switch: PHONES sends to headphones only, PHONES + MAIN sends to the rear outs too** (manual page 11). This cost us an evening.
- The Scarlett's clock fell back to Internal once during testing, cause unknown. If the digital box disappears, check that first.

## USB monitoring

The app's **MONITOR OTHER BOX** button plays the other box's 8 analog inputs, summed to mono, out of OUT 1-2 of the box on libusbK.

- It needs **libusbK** on the playing box (Zadig, VID 0582 PID 0120; Zadig swaps every box with that ID). WinUSB can't do it: it packs each write into full-size packets, and the OCTA, which expects 5 or 6 frames per microframe, crackled. libusbK lets every packet have its own size, so each one carries exactly what the box's matching capture packet delivered (implicit feedback).
- The two USB controllers differ by about 120 ppm, so a 32-tap sinc resampler follows the buffer level.
- Measured on hardware: a -50 dB test tone came back at -50.4 dB with no slips, 40 s of live monitoring had no underruns, and a 20 s take recorded alongside it had 0 dropouts.
- Delay adds up from the capture transfer (2 ms), the buffer (about 5.5 ms), the playback lead (6 ms) and the converters. It could maybe be squeezed to about 7 ms, which still isn't direct monitoring.
- Every 15 s or so the buffer level jumps by about 80 samples. No transfer is short and nothing underruns, so it shouldn't be audible, but the cause isn't known yet.

Still unproven: sample-exact sync between the two boxes. It needs a shared signal into both (two mics touching is enough).
