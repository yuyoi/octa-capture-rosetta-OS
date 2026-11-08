"""Where does the tone in a RD_MIXDUMP break, and by how much? Follows the tone's phase sample by sample."""
import collections
import sys

import numpy as np
from scipy.signal import butter, hilbert, sosfiltfilt

raw = np.fromfile(sys.argv[1], dtype=np.uint8)
raw = raw[:len(raw) // 3 * 3]
x = (raw[0::3].astype(np.int32) << 8 | raw[1::3].astype(np.int32) << 16 | raw[2::3].astype(np.int32) << 24) / 2147483648.0
fs, f = 44100.0, 997.0
env0 = np.abs(hilbert(x))
on = np.where(env0 > 10 ** (-62 / 20))[0]
a, b = on[0] + 8000, on[-1] - 8000
x = x[a:b]
print('analysed', len(x), 'samples')
sos = butter(4, [500, 3000], 'bandpass', fs=fs, output='sos')
z = hilbert(sosfiltfilt(sos, x))
ph = np.unwrap(np.angle(z)) - 2 * np.pi * f * np.arange(len(x)) / fs
slip = (ph - ph[0]) * fs / (2 * np.pi * f)          # tone position error in samples
print('slip over the run: start 0, end %.1f samples, min %.1f, max %.1f' % (slip[-1], slip.min(), slip.max()))
env = np.abs(z)
print('envelope dB: min %.1f median %.1f max %.1f' % tuple(20 * np.log10(np.percentile(env, p)) for p in (0.1, 50, 99.9)))
s = slip[::1]
d = s[64:] - s[:-64]
big = np.where(np.abs(d) > 0.5)[0]
ev, last = [], -1000
for i in big:
    if i - last > 64:
        ev.append(i)
    last = i
ev = np.array(ev)
print('slip events:', len(ev))
if len(ev) > 2:
    sp = np.diff(ev)
    print('spacing: median %d, min %d, max %d' % (np.median(sp), sp.min(), sp.max()))
    print('commonest spacings', collections.Counter((sp // 8 * 8).tolist()).most_common(8))
    print('first events (pos, jump):', [(int(i), round(float(d[i]), 2)) for i in ev[:14]])
y = x - np.real(z)
e = np.abs(np.diff(y))
th = np.percentile(e, 99.9)
print('diff residual dB: median %.1f, 99.9%% %.1f, max %.1f' % (20 * np.log10(np.median(e)), 20 * np.log10(th), 20 * np.log10(e.max())))
pk = np.where(e > max(th, 10 ** (-58 / 20)))[0]
print('click positions', pk[:24].tolist())
if len(pk) > 3:
    print('click spacing commonest', collections.Counter(np.diff(pk).tolist()).most_common(10))
