"""Host test of patches/strip.c v2 (HOST_TEST build: C multiplies) driven by the real Strip class in
tools/octa_lab.py. The Blackfin multiplies (inline asm) are tested on the unit: octa_lab.py strip selftest."""
import ctypes
import math
import struct
import sys
import types

sys.modules['mido'] = types.ModuleType('mido')          # octa_lab imports mido; not needed here
import octa_lab as ol                                   # noqa: E402

lib = ctypes.CDLL('./liblab.so')
lib.strip_init()
BASE = ctypes.addressof(ctypes.c_char.in_dll(lib, 'strip_host_mem'))
FS = 48000
FULL = 1 << 27
ok = 0


def check(c, msg):
    global ok
    if not c:
        sys.exit(f'FAIL: {msg}')
    ok += 1


class FakeLab:
    """peek/poke straight into the host block; a write to mb_seq runs one 'ISR frame' to take the mailbox."""
    def strip_addr(self):
        return BASE

    def peek(self, a, n, w=1):
        return ctypes.string_at(a, n)

    def poke(self, a, data, w=1):
        ctypes.memmove(a, data, len(data))
        if a == BASE + ol.H_SEQ:
            lib.strip_pre((ctypes.c_int32 * 12)())   # all-zero frame (only touches channels that are on)


lab = FakeLab()
st = ol.Strip(lab, FS)


def run(frames):
    out, buf = [], (ctypes.c_int32 * 12)()
    for f in frames:
        for i, v in enumerate(f):
            buf[i] = v
        lib.strip_pre(buf)
        out.append(list(buf[:10]))
    return out


def sine(f, n, amp=0.5, ch=0):
    return [[round(amp * FULL * math.sin(2 * math.pi * f * t / FS)) if c == ch else 1000 + c for c in range(10)]
            for t in range(n)]


def const(v, n, ch):
    return [[v if c == ch else 0 for c in range(10)] for _ in range(n)]


def rms_db(frames, ch=0, skip=0):
    xs = [fr[ch] for fr in frames[skip:]]
    return 20 * math.log10(math.sqrt(sum(x * x for x in xs) / len(xs)) / FULL + 1e-30)


def harmonics(frames, f0, ch=0, skip=0, n=4096):
    """dBc of harmonics 2..4 relative to the fundamental, and the DC level (dBFS)."""
    xs = [fr[ch] / FULL for fr in frames[skip:skip + n]]
    w = [0.5 - 0.5 * math.cos(2 * math.pi * i / (n - 1)) for i in range(n)]

    def amp(f):
        re = sum(x * wi * math.cos(2 * math.pi * f * i / FS) for i, (x, wi) in enumerate(zip(xs, w)))
        im = sum(x * wi * math.sin(2 * math.pi * f * i / FS) for i, (x, wi) in enumerate(zip(xs, w)))
        return math.hypot(re, im)
    a1 = amp(f0)
    dc = abs(sum(xs) / n)
    return [20 * math.log10(amp(k * f0) / a1 + 1e-12) for k in (2, 3, 4)], 20 * math.log10(dc + 1e-12)


def ref_biquad(c, xs):
    x1 = x2 = y1 = y2 = 0.0
    out = []
    for x in xs:
        y = c[0] * x + c[1] * x1 + c[2] * x2 - c[3] * y1 - c[4] * y2
        x2, x1, y2, y1 = x1, x, y1, y
        out.append(y)
    return out


# --- layout / init
magic, ver, nch, csize = struct.unpack('<4I', ctypes.string_at(BASE, 16))
check(magic == 0x53545250 and ver == 2 and nch == 8, 'header')
check(st.f['bq'] == 12 and st.bqsize == 40 and st.csize == 264, f'layout read from the block {st.f} {st.csize}')
check(all(st.rd(c, 'gin') == FULL and st.flags(c) == 0 for c in range(8)), 'init: unity gains, all off')
fr = sine(1000, 300)
check(run(fr) == fr, 'all off = bit-exact passthrough')

# --- gain
st.gain(0, 6.0, 0.0)
d = rms_db(run(sine(1000, 2400)), 0) - rms_db(sine(1000, 2400), 0)
check(abs(d - 6.0) < 0.01, f'in gain +6: {d:.3f} dB')
st.gain(0, 6.0, -6.0)
d = rms_db(run(sine(1000, 2400)), 0) - rms_db(sine(1000, 2400), 0)
check(abs(d) < 0.01, f'in +6 / out -6: {d:.3f} dB')
st.off(0, ['gain'])

# --- HPF through the mailbox vs a float reference
c = ol.rbj('hpf', FS, 100)
st.eq(0, 'hpf', c)
check(st.flags(0) == ol.F['hpf'], 'hpf flag')
hi = sine(1000, 4800)
y = run(hi)
cq = ol.q30(c)
ref = ref_biquad([cq[0] / 2**30, cq[1] / 2**30, cq[2] / 2**30, -cq[3] / 2**30, -cq[4] / 2**30], [float(f[0]) for f in hi])
err = max(abs(y[t][0] - ref[t]) for t in range(len(hi)))
check(err < 32, f'hpf matches float ref (max err {err:.1f} LSB Q27)')
d20 = rms_db(run(sine(20, 9600)), 0, 4800) - rms_db(sine(20, 9600), 0, 4800)
check(-30 < d20 < -26, f'20 Hz through HPF100: {d20:.1f} dB')
# the C model (= the unit's asm math) and the Python model in octa_lab.bq_model agree bit for bit
import random  # noqa: E402
st.off(0, ['hpf'])
cq = ol.q30(ol.rbj('peak', FS, 700, 12, 2))
st.eq(0, 'peak', ol.rbj('peak', FS, 700, 12, 2))
rnd, mst, same = random.Random(3), [0, 0, 0, 0, 0], True
for t in range(3000):
    x = rnd.choice([rnd.randrange(-(1 << 31), 1 << 31), rnd.randrange(-(1 << 27), 1 << 27), round(0.8 * FULL * math.sin(t / 7))])
    want = ol.bq_model(cq, mst, x)
    buf = (ctypes.c_int32 * 12)(x)
    lib.strip_pre(buf)
    if max(-4 * FULL, min(4 * FULL - 1, want)) != buf[0]:
        same = False
        break
check(same, f'C model == Python bq_model, 3000 samples incl. overloads (stopped at {t})')
st.off(0, ['peak'])
st.eq(0, 'hpf', c)
check(all(y[t][1] == hi[t][1] for t in range(0, 4800, 37)), 'other inputs untouched')
st.off(0, ['hpf'])

# --- tube: bias 0 = odd only, bias 0.2 = even harmonics, no DC, unity small-signal gain
st.tube(1, 12.0, 0.0, 0.0)
y = run(sine(1000, 9600, 0.5, 1))
(h2, h3, h4), dc = harmonics(y, 1000, 1, 4800)
check(h2 < -80 and -40 < h3 < -5, f'tube bias 0: H2 {h2:.0f} H3 {h3:.0f} dBc')
st.tube(1, 12.0, 0.2, 0.0)
y = run(sine(1000, 9600, 0.5, 1))
(h2, h3, h4), dc = harmonics(y, 1000, 1, 4800)
check(-40 < h2 < -5 and h3 < -5, f'tube bias 0.2: H2 {h2:.0f} H3 {h3:.0f} H4 {h4:.0f} dBc')
check(dc < -60, f'DC blocked after the asymmetric curve: {dc:.0f} dBFS')
small = sine(1000, 9600, 0.003, 1)
d = rms_db(run(small), 1, 4800) - rms_db(small, 1, 4800)
check(abs(d) < 0.2, f'tube small-signal gain {d:.2f} dB')
print(f'  tube 12 dB drive, bias 0.2, 0.5 FS sine: H2 {h2:.1f} H3 {h3:.1f} H4 {h4:.1f} dBc')
st.off(1, ['tube'])

# --- comp static curve: thr -20 dBFS (peak), ratio 4, hard knee
st.comp(2, -20, 4, 0.5, 50, 0, 0)
for lvl in (-40, -30, -20, -14, -8, -2):
    x = sine(1000, 9600, 10 ** (lvl / 20), 2)
    got = rms_db(run(x), 2, 7200) - rms_db(x, 2, 7200)
    want = 0 if lvl <= -20 else -(lvl + 20) * 0.75
    check(abs(got - want) < 0.6, f'comp 4:1 @ {lvl} dBFS: {got:.2f} dB (want {want:.2f})')
g, gmax = st.gr(2)
check(abs(gmax - 13.5) < 0.6, f'GR meter max {gmax:.2f} dB (want ~13.5)')

# --- soft knee: at threshold GR = slope * K / 8
st.off(2, ['comp'])
st.comp(2, -20, 4, 0.5, 2000, 12, 0)
run(const(round(10 ** (-20 / 20) * FULL), 4800, 2))
g, _ = st.gr(2)
check(abs(g - 0.75 * 12 / 8) < 0.05, f'soft knee 12 dB at threshold: GR {g:.3f} dB (want 1.125)')

# --- attack timing: step -40 -> -4 dBFS, GR reaches 63 % after one attack time constant
st.off(2, ['comp'])
st.comp(2, -20, 4, 10, 100, 0, 0)
run(const(round(10 ** (-40 / 20) * FULL), 2000, 2))
run(const(round(10 ** (-4 / 20) * FULL), 480, 2))      # 10 ms
g, _ = st.gr(2)
check(abs(g / 12.0 - 0.632) < 0.03, f'attack 10 ms: {g:.2f} of 12 dB after 10 ms ({g / 12:.3f})')

# --- makeup
st.comp(2, -20, 4, 10, 100, 0, 6)
x = sine(1000, 4800, 0.01, 2)
run(const(0, 48000, 2))                                 # let the GR release fully (1 s, release 100 ms)
d = rms_db(run(x), 2, 2400) - rms_db(x, 2, 2400)
check(abs(d - 6) < 0.05, f'makeup +6 below threshold: {d:.2f} dB')
st.off(2, ['comp'])

# --- limiter + clip count
st.gain(3, 23.0, 0.0)
c0 = struct.unpack('<I', ctypes.string_at(BASE + ol.H_CLIPS, 4))[0]
y = run(sine(100, 2000, 1.0, 3))
peak = max(abs(f[3]) for f in y)
check(struct.unpack('<I', ctypes.string_at(BASE + ol.H_CLIPS, 4))[0] > c0 and peak <= 4 * FULL,
      f'limiter at +12 dB holds ({peak / FULL:.2f} FS), hits counted')
st.off(None, list(ol.F))
fr = sine(440, 500, 0.9, 5)
check(run(fr) == fr, 'off all: passthrough again')

# --- full chain on one input stays sane
st.gain(4, 3, 0)
st.eq(4, 'hpf', ol.rbj('hpf', FS, 80))
st.tube(4, 6, 0.2, 0)
st.eq(4, 'low', ol.rbj('low', FS, 120, 3))
st.eq(4, 'peak', ol.rbj('peak', FS, 2500, 4, 1))
st.eq(4, 'high', ol.rbj('high', FS, 9000, 2))
st.comp(4, -18, 3, 5, 120, 6, 2)
check(st.flags(4) == 127, 'all 7 blocks on input 5')
x = sine(300, 4800, 0.3, 4)
y = run(x)
check(max(abs(f[4]) for f in y) < 2 * FULL and abs(rms_db(y, 4, 2400) - rms_db(x, 4, 2400)) < 12, 'full chain sane')

# --- magic broken: the ISR leaves the audio alone
ctypes.memmove(BASE, struct.pack('<I', 0), 4)
fr = sine(300, 200, 0.3, 4)
check(run(fr) == fr, 'bad magic = untouched audio')
lib.strip_init()
check(st.flags(4) == 0, 're-init: everything off')
print(f'STRIP v2: ALL {ok} CHECKS PASSED')
