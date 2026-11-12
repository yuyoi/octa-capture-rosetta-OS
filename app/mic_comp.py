"""Mic compensation: take a mic's frequency response graph and fit the strip's three EQ bands to correct it.

A curve is a text file in app/mics/ (NAME.csv), one "frequency Hz, dB" pair per line, # for comments. The correction is
target minus mic: towards flat, or towards another mic's curve. The bands are the real ones (low shelf, peak, high shelf),
so nothing extra runs on the box, and `left_rms` says honestly how much of the curve three bands could not follow.
Published graphs are on-axis averages, so this is tone shaping rather than exact correction.
Pure Python: the app passes its own rbj() and mag_db(), so what is fitted is exactly what gets drawn and sent.
"""
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
FOLDER = os.path.join(HERE, 'mics')
GRID = [60 * (15000 / 60) ** (i / 39) for i in range(40)]
KEYS = ('low_f', 'low_g', 'peak_f', 'peak_g', 'peak_q', 'high_f', 'high_g')
LIMITS = dict(low_f=(60, 500), peak_f=(200, 10000), peak_q=(0.4, 4), high_f=(3000, 14000))
BAND = (120, 11000)                                       # corrected range
MAX_BOOST = 4.0                                           # cutting is cheap, boosting costs headroom and raises noise


def names():
    try:
        return sorted(f[:-4] for f in os.listdir(FOLDER) if f.lower().endswith('.csv'))
    except OSError:
        return []


def parse_curve(text):
    pts = []
    for ln in text.splitlines():
        ln = ln.split('#')[0].strip()
        if not ln:
            continue
        a = ln.replace(';', ',').split(',')
        if len(a) != 2:
            raise ValueError('expected "Hz, dB": ' + ln)
        f, d = float(a[0]), float(a[1])
        if not (10 <= f <= 40000 and -60 <= d <= 60):
            raise ValueError('out of range: ' + ln)
        pts.append((f, d))
    pts.sort()
    if len(pts) < 6 or len({p[0] for p in pts}) != len(pts):
        raise ValueError('need at least 6 points at different frequencies')
    return pts


def load(name):
    with open(os.path.join(FOLDER, name + '.csv'), encoding='utf-8') as f:
        return parse_curve(f.read())


def at(curve, f):
    """value at f: straight lines on a log frequency axis, flat past the ends"""
    if f <= curve[0][0]:
        return curve[0][1]
    for (f0, d0), (f1, d1) in zip(curve, curve[1:]):
        if f <= f1:
            return d0 + (d1 - d0) * math.log(f / f0) / math.log(f1 / f0)
    return curve[-1][1]


def _weight(f):
    return 1.0 if 100 <= f <= 10000 else 0.4


def correction(mic, target=None):
    """dB to apply at each grid frequency, level taken out (EQ fixes shape, the preamp fixes level). The median sets
    the level, not the mean: one bump must not lift the flat part of the curve and send the shelves chasing it."""
    def want(f):                                          # a mic's natural roll-offs are left alone: boosting them is rumble and hiss
        g = min(BAND[1], max(BAND[0], f))
        return (at(target, g) if target else 0.0) - at(mic, g)
    raw = [want(f) for f in GRID]
    mid = sorted(x for f, x in zip(GRID, raw) if 100 <= f <= 10000)
    level = (mid[len(mid) // 2] + mid[(len(mid) - 1) // 2]) / 2
    return [x - level for x in raw]


def _clamp(k, v, max_db):
    if k in LIMITS:
        lo, hi = LIMITS[k]
    else:
        lo, hi = -max_db, min(max_db, MAX_BOOST)
    return min(hi, max(lo, v))


def fit(want, rbj, mag_db, fs=48000.0, max_db=9.0):
    """-> (params for the strip's low/peak/high, error left in dB rms, error per grid point)"""
    w = [_weight(f) for f in GRID]
    total = sum(w)

    def resp(p):
        st = [rbj('low', fs, p['low_f'], p['low_g']), rbj('peak', fs, p['peak_f'], p['peak_g'], p['peak_q']),
              rbj('high', fs, p['high_f'], p['high_g'])]
        return [mag_db(st, f) for f in GRID]

    def cost(p):
        e = sum(a * (x - y) ** 2 for a, x, y in zip(w, resp(p), want)) / total
        return e + 0.002 * (p['low_g'] ** 2 + p['peak_g'] ** 2 + p['high_g'] ** 2)       # prefer the gentler of two equal fits

    def search(p):
        best = cost(p)
        step = dict(low_f=1.5, peak_f=1.5, high_f=1.3, peak_q=1.5, low_g=3.0, peak_g=3.0, high_g=3.0)
        for _ in range(9):
            moved = True
            while moved:
                moved = False
                for k in KEYS:
                    for sign in (1, -1):
                        q = dict(p)
                        q[k] = _clamp(k, p[k] * step[k] ** sign if k in ('low_f', 'peak_f', 'high_f', 'peak_q') else p[k] + sign * step[k], max_db)
                        c = cost(q)
                        if c < best - 1e-5:
                            p, best, moved = q, c, True
                            break
            for k in step:
                step[k] = 1 + (step[k] - 1) / 2 if k in ('low_f', 'peak_f', 'high_f', 'peak_q') else step[k] / 2
        return p, best

    found = []
    for pf in (400.0, 1500.0, 5000.0):                    # three places the middle band could usefully start
        for pg in (-3.0, 3.0):
            found.append(search(dict(low_f=150.0, low_g=0.0, peak_f=pf, peak_g=pg, peak_q=1.0, high_f=8000.0, high_g=0.0)))
    p = min(found, key=lambda x: x[1])[0]
    err = [x - y for x, y in zip(resp(p), want)]
    rms = math.sqrt(sum(a * e * e for a, e in zip(w, err)) / total)
    return {k: round(v, 3) for k, v in p.items()}, rms, err


def solve(name, rbj, mag_db, target=None, fs=48000.0, max_db=9.0):
    mic = load(name)
    want = correction(mic, load(target) if target else None)
    p, rms, err = fit(want, rbj, mag_db, fs, max_db)
    before = math.sqrt(sum(_weight(f) * x * x for f, x in zip(GRID, want)) / sum(_weight(f) for f in GRID))
    return dict(params=p, want=want, left_rms=rms, was_rms=before, worst=max(abs(e) for e in err))
