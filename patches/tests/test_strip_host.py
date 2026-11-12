"""Host test of patches/strip.c v2 (HOST_TEST build: C multiplies) driven by the real Strip class in
tools/octa_lab.py. The Blackfin multiplies (inline asm) are tested on the unit: octa_lab.py strip selftest."""
import ctypes
import os
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
check(magic == 0x53545250 and ver == 4 and nch == 8, 'header')
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

# --- amp: one per box, replaces tube/comp, gain before the tone stack, speaker after
lib.strip_init()
check(st.amp0 > 0, 'amp offset published')
st.tube(2, 6.0, 0.2, 0.0)
st.comp(2, -20.0, 3.0, 10.0, 100.0, 6.0, 0.0)
st.amp(2, 'crunch')
check(st.flags(2) == ol.F['amp'], f'amp on, tube and comp off: {st.flags(2)}')
x = sine(110, 9600, 0.125, 2)
c0 = struct.unpack('<I', ctypes.string_at(BASE + ol.H_CLIPS, 4))[0]
y = run(x)
check(all(a[0] == b[0] and a[1] == b[1] for a, b in zip(x, y)), 'other inputs untouched')
pk = max(abs(f[2]) for f in y[4800:])
check(0.05 * FULL < pk < 1.2 * FULL, f'crunch level sane: {pk / FULL:.3f} FS')
check(struct.unpack('<I', ctypes.string_at(BASE + ol.H_CLIPS, 4))[0] == c0, 'no limiter hits')
h_crunch, dc = harmonics(y, 110, 2, 4800)
check(dc < -30, f'no DC from the bias: {dc:.1f} dB')
st.amp(2, 'clean')
h_clean, _ = harmonics(run(x), 110, 2, 4800)
st.amp(2, 'lead')
y_lead = run(x)
h_lead, _ = harmonics(y_lead, 110, 2, 4800)
check(h_clean[1] < h_crunch[1] < h_lead[1] + 6, f'3rd harmonic grows with gain: clean {h_clean[1]:.1f}, crunch {h_crunch[1]:.1f}, lead {h_lead[1]:.1f}')
check(max(abs(f[2]) for f in y_lead[4800:]) < 1.2 * FULL, 'lead stays inside full scale')
lo = rms_db(run(sine(40, 9600, 0.02, 2)), 2, 4800)
mid = rms_db(run(sine(1000, 9600, 0.02, 2)), 2, 4800)
top = rms_db(run(sine(12000, 9600, 0.02, 2)), 2, 4800)
check(lo < mid - 6 and top < mid - 12, f'speaker shape: 40 Hz {lo:.1f}, 1 kHz {mid:.1f}, 12 kHz {top:.1f} dB')
print(f'amp: 40 Hz {lo:.1f} / 1 kHz {mid:.1f} / 12 kHz {top:.1f} dB; 3rd harmonic clean {h_clean[1]:.1f} crunch {h_crunch[1]:.1f} lead {h_lead[1]:.1f}')
# hard clip (strip v4): sharper corner = more upper harmonics at mid gain, same ceiling, no DC
st.amp(2, 'crunch', hard=True)
check(st.flags(2) == ol.F['amp'] | ol.F['amp_hard'], f'hard flag set: {st.flags(2)}')
y_hard = run(x)
h_hard, dc = harmonics(y_hard, 110, 2, 4800)
pk_h = max(abs(f[2]) for f in y_hard[4800:])
check(h_hard[1] > h_crunch[1] + 1 and dc < -30 and pk_h < 1.2 * FULL,
      f'hard crunch: 3rd {h_hard[1]:.1f} vs soft {h_crunch[1]:.1f} dBc, DC {dc:.1f} dB, peak {pk_h / FULL:.2f} FS')
st.amp(2, 'crunch')
h_back, _ = harmonics(run(x), 110, 2, 4800)
check(st.flags(2) == ol.F['amp'] and abs(h_back[1] - h_crunch[1]) < 0.3, f'soft again = crunch as before: 3rd {h_back[1]:.1f} dBc')
st.amp(2, 'fuzz')
c0 = struct.unpack('<I', ctypes.string_at(BASE + ol.H_CLIPS, 4))[0]
y_fz = run(x)
pk_f = max(abs(f[2]) for f in y_fz[4800:])
quiet = run(sine(110, 9600, 0.004, 2))                   # -48 dBFS: a decaying note still fuzzes
check(pk_f < 1.2 * FULL and struct.unpack('<I', ctypes.string_at(BASE + ol.H_CLIPS, 4))[0] == c0,
      f'fuzz inside full scale, no limiter hits: {pk_f / FULL:.2f} FS')
check(abs(rms_db(quiet, 2, 4800) - rms_db(y_fz, 2, 4800)) < 6,
      f'fuzz sustain: -18 vs -48 dBFS in = {rms_db(y_fz, 2, 4800):.1f} / {rms_db(quiet, 2, 4800):.1f} dB out')
print(f'hard: crunch 3rd {h_hard[1]:.1f} dBc (soft {h_crunch[1]:.1f}); fuzz peak {pk_f / FULL:.2f} FS, rms {rms_db(y_fz, 2, 4800):.1f} dB')
# full GAIN on every voice: the stage split keeps both stages in range (raised before 2026-11-11)
for vname in ol.AMP_VOICES:
    st.amp(2, vname, gain_db=46.0)
    g1, g2 = ol.stage_split(46.0, ol.AMP_VOICES[vname].get('split', 0.4))
    check(abs(g1 + g2 - 46.0) < 1e-9 and max(g1, g2) <= ol.STAGE_MAX, f'{vname} at 46 dB: stages {g1:.1f} + {g2:.1f} dB')
y_t = run(x)
check(max(abs(f[2]) for f in y_t[4800:]) < 1.2 * FULL, 'full gain stays inside full scale')
st.amp(2, 'lead', tweaks=dict(tight=170.0, pre=7500.0, lp1=5800.0, lp2=8000.0, split=0.45, b1=0.2, b2=0.1, res=100.0))
check(st.flags(2) & ol.F['amp'], 'a preset\'s own amp insides go in')
# the app's factory tones (app/amp_presets.py) through the C strip: inside full scale, no limiter hits, loudness spread
try:
    import amp_presets as AP
except ImportError:
    AP = None
if AP:
    def kdb(key, v):
        return dict(amp_gain=4.6 * v, amp_pres=-3 + 1.2 * v, amp_master=-30 + 3.6 * v).get(key, (v - 5) * 2.4)
    VD = dict(clean=(12, 2, -3), crunch=(26, 3, -3), lead=(40, 4, -4), cranked=(24, 2, -3), fuzz=(46, 1, -8))
    louds = {}
    for p in AP.FACTORY:
        k = p['knobs']
        tw = p['tweaks']
        c0 = struct.unpack('<I', ctypes.string_at(BASE + ol.H_CLIPS, 4))[0]
        st.amp(2, p['voice'], kdb('amp_gain', k['amp_gain']), kdb('amp_master', k['amp_master']) + max(0.0, kdb('amp_treble', k['amp_treble'])),
               kdb('amp_pres', k['amp_pres']),
               p['hard'], {n: v for n, v in tw.items() if n in ol.AMP_TWEAKS})
        st.eq(2, 'low', ol.rbj('low', FS, 150.0, kdb('amp_bass', k['amp_bass'])))
        st.eq(2, 'peak', ol.rbj('peak', FS, tw['mid_f'], kdb('amp_mid', k['amp_mid']), tw['mid_q']))
        tg = kdb('amp_treble', k['amp_treble'])
        st.eq(2, 'high', ol.rbj('low' if tg > 0 else 'high', FS, 2800.0, -tg if tg > 0 else tg))
        loud = run(sine(110, 9600, 0.125, 2))
        soft = run(sine(110, 9600, 0.01, 2))
        pk = max(abs(f[2]) for f in loud[4800:])
        hits = struct.unpack('<I', ctypes.string_at(BASE + ol.H_CLIPS, 4))[0] - c0
        louds[p['name']] = rms_db(loud, 2, 4800)
        if os.environ.get('CALIB'):
            print(f"CALIB {p['name']} peak {pk / FULL:.3f} master_knob {k['amp_master']}")
            continue
        check(pk < 1.2 * FULL and hits == 0, f"tone {p['name']}: peak {pk / FULL:.2f} FS, rms {louds[p['name']]:.1f} dB, "
              f"-40 dBFS note {rms_db(soft, 2, 4800):.1f} dB, limiter hits {hits}")
    spread = max(louds.values()) - min(louds.values())
    print(f'tone loudness spread {spread:.1f} dB')
    st.off(2, ['low', 'peak', 'high'])
st.amp_off(2)
check(st.flags(2) & (ol.F['amp'] | ol.F['amp_hard']) == 0, 'amp off clears the hard flag')
fr = sine(300, 200, 0.3, 2)
check(run(fr) == fr, 'amp off = untouched audio')

# --- magic broken: the ISR leaves the audio alone
ctypes.memmove(BASE, struct.pack('<I', 0), 4)
fr = sine(300, 200, 0.3, 4)
check(run(fr) == fr, 'bad magic = untouched audio')
lib.strip_init()
check(st.flags(4) == 0, 're-init: everything off')
# --- FAT / SLIM
for f, db, q, kind in ((8000.0, 6.0, 0.7071, 'high'), (3000.0, -6.0, 1.2, 'peak'), (1500.0, 4.0, 1.0, 'peak')):
    c = ol.rbj(kind, FS, f, db, q)
    s = ol.slim_q30(c, FS)
    check(s is not None, f'{kind} {f} Hz is cheap enough for SLIM')
    check(all(v & 0xffff == 0 for v in s[:3]), 'SLIM coefficients have empty low halves')
    check(s[3:] == ol.q30(c)[3:], 'SLIM leaves the feedback alone')
for f, db, q, kind in ((60.0, 12.0, 2.0, 'peak'), (120.0, 9.0, 4.0, 'peak')):
    check(ol.slim_q30(ol.rbj(kind, FS, f, db, q), FS) is None, f'{kind} {f} Hz needs full precision, SLIM refuses it')

st.off(0, ['hpf', 'low', 'peak', 'high', 'tube', 'comp', 'gain'])
sig = sine(1000, 6000, 0.4)
st.eq(0, 'high', ol.rbj('high', FS, 8000.0, 6.0))
full = run(sig)
st.off(0, ['high'])
check(st.flags(0) & ol.F['slim_high'] == 0, 'turning a band off clears its SLIM bit')
st.eq(0, 'high', ol.rbj('high', FS, 8000.0, 6.0), slim=True)
check(st.flags(0) & ol.F['slim_high'], 'SLIM bit set when the filter qualifies')
slim = run(sig)
check(abs(rms_db(full, skip=2000) - rms_db(slim, skip=2000)) < 0.01, 'SLIM high shelf sounds like FAT')
st.off(0, ['high'])
st.eq(0, 'peak', ol.rbj('peak', FS, 60.0, 12.0, 2.0), slim=True)
check(st.flags(0) & ol.F['peak'] and not st.flags(0) & ol.F['slim_peak'], 'a low band in SLIM mode quietly stays full precision')
st.off(0, ['peak'])

ch = 2
lvl = 0.5
for div, name in ((1, 'FAT'), (ol.COMP_DIV, 'SLIM')):
    st.off(ch, ['comp'])
    st.comp(ch, -20.0, 4.0, 2.0, 50.0, 0.0, 0.0, div=div)
    check(bool(st.flags(ch) & ol.F['comp_div']) == (div > 1), f'{name} comp flag')
    out = run(sine(1000, 16000, lvl, ch=ch))
    if div == 1:
        fat_level = rms_db(out, ch=ch, skip=12000)
    else:
        slim_level = rms_db(out, ch=ch, skip=12000)
check(abs(fat_level - slim_level) < 1.0, f'SLIM comp settles to the same level as FAT ({fat_level:.2f} / {slim_level:.2f} dB)')
st.off(ch, ['comp'])

def grab_attack(div):
    st.off(ch, ['comp'])
    st.comp(ch, -20.0, 4.0, 5.0, 50.0, 0.0, 0.0, div=div)
    st.wr(ch, 'g', 0, 0)
    quiet = run(sine(1000, 4000, 0.01, ch=ch))
    loud = run(sine(1000, 2000, 0.5, ch=ch))
    return [20 * math.log10(max(abs(fr[ch]) for fr in loud[i:i + 48]) / FULL + 1e-9) for i in range(0, 1920, 48)]

a, b = grab_attack(1), grab_attack(ol.COMP_DIV)
worst = max(abs(x - y) for x, y in zip(a, b))
check(worst < 1.5, f'SLIM comp attack follows FAT within 1.5 dB ({worst:.2f} dB)')
st.off(ch, ['comp'])
out = run(sine(1000, 4000, 0.1, ch=5))
check(abs(rms_db(out, ch=5) - rms_db(sine(1000, 4000, 0.1, ch=5), ch=5)) < 0.001, 'an input with nothing on stays untouched')
print(f'STRIP v2: ALL {ok} CHECKS PASSED')
