"""Measure the clock drift between two OCTA-CAPTUREs running as separate devices (VS EXPAND off, no coax).
Opens one input on each unit at the same time (WASAPI shared), counts the frames each delivers against the PC clock,
and prints each unit's real rate and the difference in ppm / samples per minute.
python -I tools/dual_drift.py [seconds]"""
import sys
import threading
import time

import sounddevice as sd


def find(tag):
    for i, d in enumerate(sd.query_devices()):
        if d['max_input_channels'] >= 2 and 'WASAPI' in sd.query_hostapis(d['hostapi'])['name'] \
                and d['name'].startswith('1-2 (' + tag):
            return i
    raise SystemExit(f'no WASAPI input "1-2 ({tag}..." found')


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 30
    devs = {'unit 1': find('OCTA-CAPTURE)'), 'unit 2': find('3- OCTA-CAPTURE')}
    count = {k: 0 for k in devs}
    flags = {k: 0 for k in devs}
    first = {}
    lock = threading.Lock()

    def cb_for(k):
        def cb(indata, frames, t, status):
            now = time.perf_counter()
            if status:
                flags[k] += 1
            with lock:
                if k not in first:
                    first[k] = (now, 0)
                count[k] += frames
        return cb
    streams = []
    for k, i in devs.items():
        fs = sd.query_devices(i)['default_samplerate']
        s = sd.InputStream(device=i, channels=2, samplerate=fs, callback=cb_for(k), blocksize=2048, latency='high')
        streams.append((k, s, fs))
    for _, s, _ in streams:
        s.start()
    time.sleep(2)                                   # let both settle, then measure
    with lock:
        t0, c0 = time.perf_counter(), dict(count)
    time.sleep(secs)
    with lock:
        t1, c1 = time.perf_counter(), dict(count)
    for _, s, _ in streams:
        s.stop()
        s.close()
    rates = {}
    for k, _, fs in streams:
        rates[k] = (c1[k] - c0[k]) / (t1 - t0)
        print(f'{k}: nominal {fs:.0f} Hz, measured {rates[k]:.2f} Hz against the PC clock, {flags[k]} glitch flags')
    d = rates['unit 2'] - rates['unit 1']
    ppm = d / rates['unit 1'] * 1e6
    print(f'difference: {d:+.3f} samples/s = {ppm:+.1f} ppm = {d * 60:+.0f} samples per minute ({abs(d) * 60 / rates["unit 1"] * 1000:.1f} ms/min)')


if __name__ == '__main__':
    main()
