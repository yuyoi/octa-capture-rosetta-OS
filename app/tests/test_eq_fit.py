"""EQ boosts that don't fit the box's Q30 (HIGH from +1 dB, big wide PEAKs) go out in a form that fits, the left-out level
comes back after the comp, and the comp's threshold follows. Before 2026-11-11 they raised and dropped the link."""
import cmath
import math
import os
import sys
import types

sys.modules.setdefault('mido', types.ModuleType('mido'))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools'))
import octa_lab as ol  # noqa: E402
import octa_link as OL  # noqa: E402

FS = 48000


class FakeStrip:
    def __init__(self):
        self.fl = [0] * 8
        self.log = []

    def eq(self, ch, kind, c, slim=False):
        ol.q30(c)                                       # raises exactly like the real one if it doesn't fit
        self.log.append(('eq', ch, kind, list(c)))
        self.fl[ch] |= ol.F[kind]

    def comp(self, ch, thr, *a, **k):
        self.log.append(('comp', ch, thr))
        self.fl[ch] |= ol.F['comp']

    def gain(self, ch, din, dout):
        self.log.append(('gain', ch, din, dout))
        self.fl[ch] |= ol.F['gain']

    def flags(self, ch):
        return self.fl[ch]

    def set_flags(self, ch, v):
        self.fl[ch] = v

    def off(self, ch, names):
        for n in names:
            self.fl[ch] &= ~ol.F[n]


def H(c, f):
    b0, b1, b2, a1, a2 = c[:5]
    z = cmath.exp(-2j * math.pi * f / FS)
    return (b0 + b1 * z + b2 * z * z) / (1 + a1 * z + a2 * z * z)


def mk_link():
    L = OL.Link.__new__(OL.Link)                       # no thread, no Qt signals needed for _apply
    L.strip, L.fs, L.mode, L.fast_on = FakeStrip(), FS, 'fat', False
    L.makeup = [dict(high=0.0, peak=0.0) for _ in range(8)]
    L.last_c = [None] * 8
    return L


fails = 0


def check(ok, what):
    global fails
    print(('PASS ' if ok else 'FAIL ') + what)
    fails += not ok


def test_all():
    L = mk_link()
    c = dict(high_f=4000.0, high_g=6.0, peak_f=5000.0, peak_g=14.0, peak_q=0.3, thresh=-20.0, ratio=4.0, attack=10.0,
             release=150.0, makeup=0.0)
    L._apply(0, 'comp', c)
    L._apply(0, 'high', c)
    eqs = [x for x in L.strip.log if x[0] == 'eq' and x[2] == 'high']
    k = eqs[-1][3]
    ref = ol.rbj('high', FS, 4000.0, 6.0)
    err = max(abs(20 * math.log10(abs(H(k, f))) + 6.0 - 20 * math.log10(abs(H(ref, f)))) for f in (30, 300, 3000, 8000, 20000))
    check(err < 1e-6, f'HIGH +6 dB @4 kHz sent as a curve that fits; with the +6 dB makeup it is the same response ({err:.1e} dB)')
    check(('gain', 0, 0.0, 6.0) in L.strip.log, 'the +6 dB comes back as strip output gain')
    comps = [x for x in L.strip.log if x[0] == 'comp']
    check(comps[-1][2] == -26.0, f'comp re-sent with its threshold 6 dB lower ({comps[-1][2]})')
    L._apply(0, 'peak', c)
    pk = [x for x in L.strip.log if x[0] == 'eq' and x[2] == 'peak'][-1][3]
    refp = ol.rbj('peak', FS, 5000.0, 14.0, 0.3)
    mkp = L.makeup[0]['peak']
    errp = max(abs(20 * math.log10(abs(H(pk, f))) + mkp - 20 * math.log10(abs(H(refp, f)))) for f in (30, 300, 3000, 8000, 20000))
    check(mkp > 0 and errp < 1e-6, f'PEAK +14 dB Q0.3 @5 kHz fits after halving its gain; {mkp:.2f} dB made up ({errp:.1e} dB)')
    check(L.strip.log[-2][0] == 'gain' and abs(L.strip.log[-2][3] - (6.0 + mkp)) < 1e-9, 'makeups add up in one output gain')
    L.strip.off(0, ['high'])
    L._makeup(0, 'high', 0.0)
    check(abs([x for x in L.strip.log if x[0] == 'comp'][-1][2] - (-20.0 - mkp)) < 1e-9, 'HIGH off: its makeup and threshold shift go')
    L._makeup(0, 'peak', 0.0)
    check(not L.strip.fl[0] & ol.F['gain'] and [x for x in L.strip.log if x[0] == 'comp'][-1][2] == -20.0,
          'all boosts off: output gain off, threshold back to -20')
    L2 = mk_link()
    L2._apply(1, 'high', dict(high_f=8000.0, high_g=-6.0))
    check(not L2.strip.fl[1] & ol.F['gain'] and L2.strip.log[-1][3] == ol.rbj('high', FS, 8000.0, -6.0),
          'a HIGH cut goes out as before, no makeup')
    L2._apply(1, 'high', dict(high_f=2800.0, high_g=-7.2, high_kind='low'))
    check(not L2.strip.fl[1] & ol.F['gain'], 'the amp TREBLE path keeps its own MASTER makeup')
    for f in (1000.0, 4000.0, 16000.0):                 # every HIGH boost in the app's range now fits
        for g in (1.0, 6.0, 15.0):
            L3 = mk_link()
            L3._apply(0, 'high', dict(high_f=f, high_g=g))
    check(True, 'HIGH 1-16 kHz, +1..+15 dB: all fit (would have raised before)')
    assert not fails


if __name__ == '__main__':
    test_all()
    print('ALL PASS' if not fails else 'FAILED')
