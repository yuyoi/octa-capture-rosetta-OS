"""Two OCTA-CAPTUREs as separate devices (VS EXPAND off, no coax, each on its own USB controller):
stream one input of each at the same time and measure each unit's true sample rate against the PC clock
(least-squares fit over every callback), then the drift between the two units.
python -I tools/dual_drift.py [seconds]      (default 60)"""
import sys
import threading
import time

import sounddevice as sd


def find():
    devs = {}
    for i, d in enumerate(sd.query_devices()):
        if d['max_input_channels'] and 'WASAPI' in sd.query_hostapis(d['hostapi'])['name'] \
                and d['name'].startswith('1-2 (') and 'OCTA' in d['name']:
            devs['unit 2' if '(3-' in d['name'] or '(2-' in d['name'] else 'unit 1'] = i
    if len(devs) != 2:
        raise SystemExit(f'need both units as separate WASAPI devices, found {devs}')
    return devs


def fit(pts):
    n = len(pts)
    mt = sum(t for t, _ in pts) / n
    mc = sum(c for _, c in pts) / n
    sxx = sum((t - mt) ** 2 for t, _ in pts)
    return sum((t - mt) * (c - mc) for t, c in pts) / sxx


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 60
    devs = find()
    pts = {k: [] for k in devs}
    cnt = {k: 0 for k in devs}
    lock = threading.Lock()
    t_start = [None]

    def mk(k):
        def cb(indata, frames, t, status):
            now = time.perf_counter()
            with lock:
                cnt[k] += frames
                if t_start[0] is not None:
                    pts[k].append((now - t_start[0], cnt[k]))
        return cb
    ss = [sd.InputStream(device=i, channels=2, samplerate=44100, callback=mk(k)) for k, i in devs.items()]
    for s in ss:
        s.start()
    time.sleep(2)
    t_start[0] = time.perf_counter()
    time.sleep(secs)
    for s in ss:
        s.stop()
        s.close()
    r = {k: fit(v) for k, v in pts.items()}
    for k in sorted(r):
        print(f'{k}: {r[k]:.3f} Hz ({(r[k] / 44100 - 1) * 1e6:+.1f} ppm vs the PC clock), {len(pts[k])} callbacks')
    d = r['unit 2'] - r['unit 1']
    print(f'drift unit 2 vs unit 1: {d:+.3f} samples/s = {d / r["unit 1"] * 1e6:+.1f} ppm '
          f'= {d * 60:+.0f} samples/min = {d * 60 / 44.1:+.1f} ms/min')


if __name__ == '__main__':
    main()
