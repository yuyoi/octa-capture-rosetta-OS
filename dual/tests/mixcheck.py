"""Checks a RD_MIXDUMP capture (raw 24-bit mono at 44.1k) of the 997 Hz test tone: per 2048-sample window it fits the tone
and reports the level, what is left after removing it, and any jump in the tone's phase (a slip = lost or doubled audio)."""
import sys

import numpy as np

raw = np.fromfile(sys.argv[1], dtype=np.uint8)
raw = raw[:len(raw) // 3 * 3]
x = (raw[0::3].astype(np.int32) << 8 | raw[1::3].astype(np.int32) << 16 | raw[2::3].astype(np.int32) << 24) / 2147483648.0
fs, f, W = 44100.0, 997.0, 2048
n = np.arange(W) / fs
A = np.column_stack((np.sin(2 * np.pi * f * n), np.cos(2 * np.pi * f * n), np.ones(W)))
lev, res, ph, clicks = [], [], [], []
for i in range(0, len(x) - W, W):
    w = x[i:i + W]
    c, *_ = np.linalg.lstsq(A, w, rcond=None)
    e = w - A @ c
    lev.append(np.hypot(c[0], c[1]))
    res.append(np.sqrt(np.mean(e ** 2)))
    ph.append(np.arctan2(c[1], c[0]))
    clicks.append(np.max(np.abs(np.diff(e))))
lev, res, ph, clicks = map(np.array, (lev, res, ph, clicks))
on = lev > 10 ** (-60 / 20)
print('seconds', round(len(x) / fs, 2), 'windows', len(lev), 'tone windows', int(on.sum()))
if on.any():
    idx = np.where(on)[0][2:-2]
    print('tone peak dB  min %.2f  max %.2f' % (20 * np.log10(lev[idx].min()), 20 * np.log10(lev[idx].max())))
    print('residual rms dB  median %.1f  worst %.1f' % (20 * np.log10(np.median(res[idx])), 20 * np.log10(res[idx].max())))
    print('largest step in residual dB  median %.1f  worst %.1f' % (20 * np.log10(np.median(clicks[idx])), 20 * np.log10(clicks[idx].max())))
    adv = 2 * np.pi * f * W / fs
    d = np.angle(np.exp(1j * (ph[idx][1:] - ph[idx][:-1] - adv)))
    d -= np.median(d)
    print('phase jump in samples  worst %.2f  windows over 0.25: %d' % (np.abs(d).max() * fs / (2 * np.pi * f), int((np.abs(d) * fs / (2 * np.pi * f) > 0.25).sum())))
