"""Rosetta Lab for the OCTA-CAPTURE: talk to the running firmware over the USB CTRL MIDI port.

Needs a unit running a Rosetta build with the lab backend (patches/lab1.S). Protocol: docs/LAB.md.

  python tools/octa_lab.py ping
  python tools/octa_lab.py peek ADDR [N] [--w 1|2|4]      hex dump (ADDR hex, N bytes, default 64)
  python tools/octa_lab.py poke ADDR HEXBYTES [--w 1|2|4]
  python tools/octa_lab.py dump ADDR N FILE               read N bytes to a file
  python tools/octa_lab.py load ADDR FILE                 write a file to memory (verified by sum)
  python tools/octa_lab.py sum ADDR N                     32-bit word sum on the unit (fast compare)
  python tools/octa_lab.py fill ADDR N WORD               fill N bytes with a 32-bit pattern
  python tools/octa_lab.py check ADDR N WORD              count words != pattern, first bad address
  python tools/octa_lab.py call ADDR [R0 R1 R2]           flush I-cache over the lab code area, call, print R0
  python tools/octa_lab.py meter                          DSP load: busy / period cycles, CCLK estimate
  python tools/octa_lab.py meter show|hide                load numbers on the LCD (lab >= 1.6: hidden by default)
  python tools/octa_lab.py chop on|off                    record-ch1 chopper (audible test)
  python tools/octa_lab.py strip                          channel strip status (lab >= 1.4, docs/STRIP.md)
  python tools/octa_lab.py strip gain CH IN_DB [OUT_DB]   digital in/out gain (CH = input 1-8)
  python tools/octa_lab.py strip hpf CH FREQ [Q]          high-pass 12 dB/oct
  python tools/octa_lab.py strip low|peak|high CH FREQ DB [Q]   shelves / peak
  python tools/octa_lab.py strip tube CH DRIVE_DB [BIAS 0.2] [OUT_DB]   tube saturation (bias = even harmonics)
  python tools/octa_lab.py strip comp CH [THR -18] [RATIO 4] [ATK 10] [REL 150] [KNEE 6] [MAKEUP 0]
  python tools/octa_lab.py strip gr CH                    comp gain reduction (now / max since last read)
  python tools/octa_lab.py strip off CH [stage] | off all
  python tools/octa_lab.py strip selftest                 on-unit multiply tests
  python tools/octa_lab.py page [unload]                  live STRIP page: upload patches/page.bin, hijack screen 18
  python tools/octa_lab.py strip bqtest                   on-unit asm biquad vs the Python model (bit-exact)
    strip options: --fs N (default: from the meter period; 44.1/48/88.2k only, 96k+ runs stock)
  python tools/octa_lab.py memprobe                       SDRAM geometry check + canaries in the free areas
  python tools/octa_lab.py memcheck                       count canary words that changed since memprobe
  python tools/octa_lab.py ports                          list MIDI ports

Options: --unit N (0 = first OCTA, 1 = second), --force (allow addresses outside the known-safe map).
Peek/poke outside mapped memory can crash the unit: then power-cycle it (nothing is written to flash).
"""
import argparse
import math
import struct
import sys
import time

import mido

MAN = [0x7D, 0x4F]          # non-commercial ID, 'O'
CMD = dict(PING=0x01, PEEK=0x02, POKE=0x03, CALL=0x04, FILL=0x05, CHECK=0x06, SUM=0x07, FLUSH=0x08)
MAXCHUNK = 64               # bytes per PEEK/POKE message (each byte = 2 nibbles)

# known-safe ranges (from re/RAM_MAP.md); everything else needs --force
SAFE = [
    (0x00000000, 0x00800000, 'SDRAM (2 MB real: 1 MB at 0x300000 code, 1 MB at 0x400000 data, mirrored; 0x800000+ is NOT safe)'),
    (0xFF800000, 0xFF804000, 'L1 data A (SRAM half)'),
    (0xFF900000, 0xFF904000, 'L1 data B (SRAM half, task stacks)'),
    (0xFFB00000, 0xFFB01000, 'scratchpad (interrupt + kernel stacks)'),
    (0xFFC00000, 0xFFE00000, 'system MMRs (use --w 2/4)'),
    (0xFFE00000, 0xFFE10000, 'core MMRs (use --w 4)'),
]   # NOT 0xFFA0xxxx: normal loads/stores to L1 code fault (no DCPLB); the unit then hangs until a power cycle
    # NOT 0x800000-0xFFFFFF: controller banks 2/3 land on the chip's banks 0/1 with the wrong open row, so even a
    # read there can make later code fetches return garbage (undefined instruction / hard hang). Proven 2026-11-07.
VARS = 0x41D5E4             # meter vars (same as dsp1): +0 ARMED +4 LAST +8 PER +0xC BMAX +0x10 FRAMES +0x14 FLAGS


EXCAUSE = {0x21: 'undefined instruction', 0x22: 'illegal instruction combination',
           0x23: 'data access CPLB protection violation', 0x24: 'misaligned data access',
           0x25: 'unrecoverable event', 0x26: 'data CPLB miss', 0x27: 'data CPLB multiple hits',
           0x28: 'emulation watchpoint', 0x2A: 'misaligned instruction', 0x2B: 'instruction CPLB protection',
           0x2C: 'instruction CPLB miss', 0x2D: 'instruction CPLB multiple hits',
           0x2E: 'illegal use of supervisor resource'}


class LabFault(SystemExit):
    """The unit caught a CPU exception while running a lab command (lab >= 1.1). The unit is still alive."""
    def __init__(self, seqstat, addr, status, retx):
        self.seqstat, self.addr, self.status, self.retx = seqstat, addr, status, retx
        self.excause = seqstat & 0x3F
        super().__init__(f'FAULT: {EXCAUSE.get(self.excause, "?")} (EXCAUSE {self.excause:#x}, SEQSTAT {seqstat:#x}), '
                         f'DCPLB_FAULT_ADDR {addr:#010x}, DCPLB_STATUS {status:#x}, at {retx:#010x}')


def nib(v, n):
    return [(v >> (4 * (n - 1 - k))) & 0xF for k in range(n)]


def unnib(d):
    return bytes((d[i] << 4) | d[i + 1] for i in range(0, len(d) - 1, 2))


def is_safe(a, n):
    return any(lo <= a and a + n <= hi for lo, hi, _ in SAFE)


class Lab:
    def __init__(self, unit=0, force=False, ports=None):
        self.force = force
        if ports:                               # (out, in) port objects from elsewhere (app/octa_engine.py, WinUSB path)
            self.out, self.inp = ports
            self.seq = 0
            return
        outs = [p for p in mido.get_output_names() if 'CTRL' in p and 'OCTA' in p]
        ins = [p for p in mido.get_input_names() if 'CTRL' in p and 'OCTA' in p]
        if len(outs) <= unit or len(ins) <= unit:
            raise SystemExit(f'no OCTA-CAPTURE CTRL port #{unit}. Ports: {mido.get_output_names()}')
        # unit 0 = the plain 'CTRL (OCTA-CAPTURE)'; the second unit is 'CTRL (EXP OCTA-CAPTURE)' (VS EXPAND) or
        # '3- CTRL (OCTA-CAPTURE)' (Windows numbers a second identical device) -> unit 1
        order = lambda p: ('EXP' in p or p[:1].isdigit(), p)
        self.out = mido.open_output(sorted(outs, key=order)[unit])
        self.inp = mido.open_input(sorted(ins, key=order)[unit])
        self.seq = 0

    def guard(self, a, n):
        if not self.force and not is_safe(a, n):
            raise SystemExit(f'{a:#010x}+{n:#x} is outside the known-safe map (use --force)')

    def xfer(self, cmd, body=(), timeout=3.0):
        self.seq = (self.seq + 1) & 0x7F
        for _ in self.inp.iter_pending():
            pass
        self.out.send(mido.Message('sysex', data=MAN + [cmd, self.seq] + list(body)))
        end = time.time() + timeout
        while time.time() < end:
            for m in self.inp.iter_pending():
                d = list(m.data) if m.type == 'sysex' else []
                if d[:2] == MAN and len(d) >= 4 and d[3] == self.seq:
                    if d[2] == 0x7F:
                        raise LabFault(*struct.unpack('>4I', unnib(d[4:])[:16]))
                    if d[2] == (cmd | 0x40):
                        return unnib(d[4:])
            time.sleep(0.002)
        raise SystemExit(f'no reply to cmd {cmd:#x} (Rosetta lab build running? right unit?)')

    # --- primitives ---
    def ping(self):
        r = self.xfer(CMD['PING'])
        ver, vars_, area = struct.unpack('>HII', r[:10])
        return ver, vars_, area

    def gate_addr(self):
        """the noise gate block (lab >= 1.8), or 0 on older firmware"""
        r = self.xfer(CMD['PING'])
        return struct.unpack('>I', r[22:26])[0] if len(r) >= 26 else 0

    def fx_addr(self):
        """the echo + reverb block (lab >= 1.8), or 0 on older firmware"""
        r = self.xfer(CMD['PING'])
        return struct.unpack('>I', r[18:22])[0] if len(r) >= 22 else 0

    def strip_addr(self):
        r = self.xfer(CMD['PING'])
        if len(r) < 14:
            raise SystemExit('this lab build has no channel strip (needs lab >= 1.3)')
        return struct.unpack('>I', r[10:14])[0]

    def peek(self, a, n, w=1):
        self.guard(a, n)
        if a % w or n % w:
            raise SystemExit(f'width {w} needs aligned address and length (misaligned access faults the CPU)')
        out = b''
        while n > 0:
            k = min(n, MAXCHUNK)
            r = self.xfer(CMD['PEEK'], nib(a, 8) + nib(k, 2) + nib(w, 1))
            out += r[:k]
            a += k
            n -= k
        return out

    def poke(self, a, data, w=1):
        self.guard(a, len(data))
        if a % w or len(data) % w:
            raise SystemExit(f'width {w} needs aligned address and length (misaligned access faults the CPU)')
        for i in range(0, len(data), MAXCHUNK):
            c = data[i:i + MAXCHUNK]
            body = nib(a + i, 8) + nib(len(c), 2) + nib(w, 1)
            for b in c:
                body += nib(b, 2)
            self.xfer(CMD['POKE'], body)

    def fill(self, a, n, word):
        self.guard(a, n)
        assert a % 4 == 0 and n % 4 == 0, 'word aligned only'
        self.xfer(CMD['FILL'], nib(a, 8) + nib(n, 8) + nib(word, 8), timeout=10)

    def check(self, a, n, word):
        self.guard(a, n)
        r = self.xfer(CMD['CHECK'], nib(a, 8) + nib(n, 8) + nib(word, 8), timeout=10)
        return struct.unpack('>II', r[:8])          # mismatches, first bad address (0xFFFFFFFF = none)

    def sum(self, a, n):
        self.guard(a, n)
        return struct.unpack('>I', self.xfer(CMD['SUM'], nib(a, 8) + nib(n, 8), timeout=10)[:4])[0]

    def call(self, a, r0=0, r1=0, r2=0):
        r = self.xfer(CMD['CALL'], nib(a, 8) + nib(r0, 8) + nib(r1, 8) + nib(r2, 8), timeout=10)
        return struct.unpack('>II', r[:8])          # R0, cycles

    def flush(self, a, n):
        self.xfer(CMD['FLUSH'], nib(a, 8) + nib(n, 8))


def hexdump(a, data):
    for i in range(0, len(data), 16):
        row = data[i:i + 16]
        print(f'{a + i:08x}  {row.hex(" "):<47}  {"".join(chr(c) if 32 <= c < 127 else "." for c in row)}')


CANARY = 0x5A5AA5A5


def sdram_geometry(lab):
    """Proves the map in re/RAM_MAP.md (2 MB chip: 2 banks x 1 MB, A20/A21 ignored, 0x800000+ forbidden) without
    touching 0x800000+: a marker at the top of the upload area (0x3ffffc) must show at its mirrors in bank 0 and
    NOT in bank 1. Only the free word itself is written, and it is restored."""
    rd = lambda x: struct.unpack('<I', lab.peek(x, 4, 4))[0]  # noqa: E731
    T, MIRRORS, OTHER = 0x003FFFFC, (0x000FFFFC, 0x001FFFFC, 0x002FFFFC), (0x004FFFFC, 0x005FFFFC)
    old, others = rd(T), [rd(x) for x in OTHER]
    mark = old ^ 0xA5C3E10F
    lab.poke(T, struct.pack('<I', mark), 4)
    try:
        missing = [x for x in MIRRORS if rd(x) != mark]
        leaked = [x for x, v in zip(OTHER, others) if rd(x) != v]
    finally:
        lab.poke(T, struct.pack('<I', old), 4)
    if missing or leaked:
        raise SystemExit(f'geometry NOT as mapped: no mirror at {[hex(x) for x in missing]}, leaked into {[hex(x) for x in leaked]}')
    return 2 << 20


def probe_areas():
    """Areas re/RAM_MAP.md calls free. Bank 1 (0x400000-0x4FFFFF) is all data + heap; bank 0 holds main's code."""
    return [(0x003B0000, 0x00050000, 'bank 0 tail (uploads)'), (0xFF8034B8, 0x00000B48, 'L1 data A tail')]


def memprobe(lab):
    """Geometry check, then canaries over the areas RAM_MAP.md calls free."""
    print(f'SDRAM: {sdram_geometry(lab) >> 20} MB real (2 banks x 1 MB, A20/A21 mirrors), geometry as mapped')
    for base, n, name in probe_areas():
        lab.fill(base, n, CANARY)
        print(f'canary set: {name} {base:#x} +{n:#x}')
    print('now use the unit normally (play, record, switch screens and rates), then: octa_lab.py memcheck')


def memcheck(lab):
    for base, n, name in probe_areas():
        bad, first = lab.check(base, n, CANARY)
        print(f'{name:18s} {base:#010x} +{n:#x}: {bad} words changed' + (f', first at {first:#x}' if bad else ''))


# ---- channel strip v2 (patches/strip.c, docs/STRIP.md) ----
CCLK = 372.4e6                  # measured on hardware (meter period x 44.1 kHz)
F = dict(hpf=1, low=2, peak=4, high=8, tube=16, comp=32, gain=64, amp=128, slim_peak=256, slim_high=512, comp_div=1024,
         amp_hard=2048, fx=4096, gate=8192)
FX_MAGIC, FX_DMAX = 0x31584652, 60000   # strip.h struct fx
GATE_MAGIC = 0x31544147                 # strip.h struct gate: magic, hold, att, rel, then 8 x (open close env g cnt is_open)
COMP_DIV = 8                                                     # SLIM: comp detector every 8 samples (patches/strip.h)
NBQ = 5                                                          # amp biquads take mailbox slots NBQ..NBQ+5
AMP_FIELDS = dict(ch=0, d1=4, b1=8, c1=12, d2=16, b2=20, c2=24, out=28)
# gain = total of both stages (dB), b1/b2 = bias, tight/pre = low-cut between the stages / top cut before them (Hz),
# res = speaker low resonance (Hz), pres = cone presence peak (dB), master = level after the speaker (dB)
AMP_VOICES = dict(
    clean=dict(gain=12.0, b1=0.10, b2=0.05, tight=80.0, pre=10000.0, res=95.0, pres=2.0, master=-3.0),
    crunch=dict(gain=26.0, b1=0.25, b2=0.12, tight=120.0, pre=8000.0, res=100.0, pres=3.0, master=-3.0),
    lead=dict(gain=40.0, b1=0.30, b2=0.15, tight=170.0, pre=6500.0, res=105.0, pres=4.0, master=-4.0),
    # the user's favourite on the Les Paul (2026-11-08): the strip's tube at 24 dB drive, bias 0.3, as a single stage
    cranked=dict(gain=24.0, split=1.0, b1=0.30, b2=0.0, tight=80.0, pre=12000.0, res=95.0, pres=2.0, master=-3.0),
    # boosted big-box fuzz for sludge (user, 2026-11-11): two symmetric stages flat out, hard, bass kept, fizz rolled off
    # before the clip; the mid scoop is the tone stack's MIDDLE (app: 1 kHz, starts scooped)
    fuzz=dict(gain=46.0, split=0.5, b1=0.0, b2=0.0, tight=55.0, pre=4000.0, res=75.0, pres=1.0, master=-8.0, hard=True),
)
SLOT = dict(hpf=0, low=1, peak=2, high=3, dc=4)
AMP_TWEAKS = ('tight', 'pre', 'res', 'b1', 'b2', 'split', 'lp1', 'lp2', 'hard')
STAGE_MAX = 23.9                        # dB one gain stage can take (Q27 drive < 16)


def stage_split(gain, split):
    """the amp gain over the two stages as the voice wants it, but neither past STAGE_MAX: the rest goes to the other
    stage, so the total is always what was asked (up to 46 dB). Before 2026-11-11 GAIN > ~40 dB on a 0.4 split raised
    here and dropped the link."""
    g1 = min(gain * split, STAGE_MAX)
    g2 = gain - g1
    if g2 > STAGE_MAX:
        g1, g2 = gain - STAGE_MAX, STAGE_MAX
    return g1, g2
FIELDS = ['flags', 'gin', 'gout', 'bq', 'drive', 'bias', 'c0', 'tout', 'thr', 'khalf', 'inv2k', 'slope',
          'atk', 'rel', 'makeup', 'g', 'grmax']
H_CLIPS, H_SEQ, H_DONE, H_TGT, H_C = 96, 100, 104, 108, 112     # fixed header (strip.c struct strip)
LOG2DB = 20 * 0.30102999566                                     # dB per log2 unit
Q24, Q27, Q30 = 1 << 24, 1 << 27, 1 << 30


def rbj(kind, fs, f, db=0.0, q=0.7071):
    """RBJ cookbook biquad, normalised to a0 = 1: [b0, b1, b2, a1, a2] as floats."""
    import math
    w = 2 * math.pi * f / fs
    cw, sw = math.cos(w), math.sin(w)
    al = sw / (2 * q)
    A = 10 ** (db / 40)
    if kind == 'lpf':
        b = [(1 - cw) / 2, 1 - cw, (1 - cw) / 2]
        a = [1 + al, -2 * cw, 1 - al]
    elif kind == 'hpf':
        b = [(1 + cw) / 2, -(1 + cw), (1 + cw) / 2]
        a = [1 + al, -2 * cw, 1 - al]
    elif kind == 'peak':
        b = [1 + al * A, -2 * cw, 1 - al * A]
        a = [1 + al / A, -2 * cw, 1 - al / A]
    elif kind in ('low', 'high'):
        s2 = 2 * math.sqrt(A) * al
        sg = 1 if kind == 'low' else -1
        b = [A * ((A + 1) - sg * (A - 1) * cw + s2), sg * 2 * A * ((A - 1) - sg * (A + 1) * cw),
             A * ((A + 1) - sg * (A - 1) * cw - s2)]
        a = [(A + 1) + sg * (A - 1) * cw + s2, -sg * 2 * ((A - 1) + sg * (A + 1) * cw),
             (A + 1) + sg * (A - 1) * cw - s2]
    elif kind == 'dc':                                          # 1st-order DC blocker at f
        pz = math.exp(-2 * math.pi * f / fs)
        return [(1 + pz) / 2, -(1 + pz) / 2, 0.0, -pz, 0.0]
    else:
        raise SystemExit(f'unknown stage {kind}')
    return [b[0] / a[0], b[1] / a[0], b[2] / a[0], a[1] / a[0], a[2] / a[0]]


def q30(coefs):
    """[b0, b1, b2, a1, a2] floats -> the unit's Q30 b0 b1 b2 -a1 -a2."""
    out = [round(c * Q30) for c in coefs[:3]] + [round(-coefs[3] * Q30), round(-coefs[4] * Q30)]
    if not all(-(1 << 31) <= v < (1 << 31) for v in out):
        raise SystemExit('a coefficient does not fit Q30 (|c| < 2): use a smaller gain or a wider Q')
    return out


def slim_q30(coefs, fs, limit_db=0.05):
    """SLIM mode: b0-b2 with their low 16 bits zero, or None if that would move the response by more than limit_db anywhere.
    Filters low in frequency need the precision, because the zeros sit close to z = 1; those stay full."""
    import cmath
    full = q30(coefs)
    slim = [((v + 0x8000) >> 16) << 16 for v in full[:3]] + full[3:]
    if not all(-(1 << 31) <= v < (1 << 31) for v in slim):
        return None

    def mag(c, z):
        n = (c[0] + c[1] * z + c[2] * z * z) / Q30
        d = 1 - (c[3] * z + c[4] * z * z) / Q30
        return abs(n / d)
    for i in range(200):
        f = 20 * ((fs * 0.45) / 20) ** (i / 199)
        z = cmath.exp(-2j * math.pi * f / fs)
        a, b = mag(full, z), mag(slim, z)
        if a <= 0 or b <= 0 or abs(20 * math.log10(b / a)) > limit_db:
            return None
    return slim


def db2q27(db, lim=15.99):
    g = 10 ** (db / 20)
    if g > lim:
        raise SystemExit(f'{db:+.1f} dB is more than this stage can do (+{20 * math.log10(lim):.1f} dB max)')
    return round(g * Q27)


def onepole(ms, fs):
    import math
    return round((1 - math.exp(-1000 / (ms * fs))) * Q30)




class Strip:
    """The unit's strip block, layout read from the block itself."""
    def __init__(self, lab, fs=0):
        self.lab, self._fs = lab, fs
        self.base = lab.strip_addr()
        magic, ver, nch, csize = struct.unpack('<4I', lab.peek(self.base, 16, 4))
        if magic != 0x53545250 or ver not in (2, 3, 4):
            raise SystemExit(f'no strip v2/v3 at {self.base:#x} (magic {magic:#x}, ver {ver}): needs lab >= 1.4')
        off = struct.unpack('<20I', lab.peek(self.base + 16, 80, 4))
        self.nch, self.csize, self.ch0, self.bqsize = nch, csize, off[0], off[1]
        self.amp0 = off[19] if ver >= 3 else 0                  # 0 = this firmware has no amp
        self.hard_ok = ver >= 4                                 # F_AMP_HARD understood (older firmware ignores the bit)
        self.f = dict(zip(FIELDS, off[2:2 + len(FIELDS)]))

    @property
    def fs(self):
        if not self._fs:
            self._fs = strip_fs(self.lab, 0)
        return self._fs

    def addr(self, ch, field, slot=None):
        a = self.base + self.ch0 + ch * self.csize + self.f[field]
        return a if slot is None else a + slot * self.bqsize

    def rd(self, ch, field):
        return struct.unpack('<i', self.lab.peek(self.addr(ch, field), 4, 4))[0]

    def wr(self, ch, field, *vals):
        self.lab.poke(self.addr(ch, field), struct.pack(f'<{len(vals)}i', *vals), 4)

    def flags(self, ch):
        return self.rd(ch, 'flags') & 0xFFFFFFFF

    def set_flags(self, ch, v):
        self.lab.poke(self.addr(ch, 'flags'), struct.pack('<I', v), 4)

    def coefs(self, ch, slot, c, reset):
        """Biquad coefficients through the mailbox (the ISR copies them between two samples)."""
        lab = self.lab
        done = struct.unpack('<I', lab.peek(self.base + H_DONE, 4, 4))[0]
        lab.poke(self.base + H_C, struct.pack('<5i', *c), 4)
        lab.poke(self.base + H_TGT, struct.pack('<I', (ch << 4) | slot | (0x100 if reset else 0)), 4)
        lab.poke(self.base + H_SEQ, struct.pack('<I', (done + 1) & 0xFFFFFFFF), 4)
        t0 = time.time()
        while struct.unpack('<I', lab.peek(self.base + H_DONE, 4, 4))[0] != (done + 1) & 0xFFFFFFFF:
            if time.time() - t0 > 1:
                raise SystemExit('the strip did not take the coefficients: is audio running at 44.1/48/88.2 kHz?')

    def eq(self, ch, kind, c, slim=False):
        """slim=True: use the cheap filter for peak/high when slim_q30 says nobody could hear the difference"""
        on = self.flags(ch)
        bit = F.get('slim_' + kind, 0)
        cheap = slim_q30(c, self.fs) if slim and bit else None
        self.set_flags(ch, on & ~bit)
        self.coefs(ch, SLOT[kind], cheap or q30(c), reset=not on & F[kind])
        self.set_flags(ch, (on & ~bit) | F[kind] | (bit if cheap else 0))

    def gain(self, ch, din, dout):
        self.wr(ch, 'gin', db2q27(din))
        self.wr(ch, 'gout', db2q27(dout))
        self.set_flags(ch, self.flags(ch) | F['gain'])

    def tube(self, ch, drive_db, bias, out_db):
        drive = 10 ** (drive_db / 20)
        if not 0 <= bias <= 0.6:
            raise SystemExit('bias 0..0.6')
        c0 = bias - bias ** 3 / 3
        tout = 10 ** (out_db / 20) / (drive * (1 - bias * bias))      # small-signal gain 1 (+ out_db)
        on = self.flags(ch)
        self.wr(ch, 'drive', db2q27(drive_db), round(bias * Q27), round(c0 * Q27), round(min(tout, 15.99) * Q27))
        if not on & F['tube']:
            self.coefs(ch, SLOT['dc'], q30(rbj('dc', self.fs, 10)), reset=True)
        self.set_flags(ch, on | F['tube'])

    def comp(self, ch, thr_db, ratio, atk_ms, rel_ms, knee_db, makeup_db, div=1):
        """div=COMP_DIV: SLIM comp, gain worked out every 8 samples, so the time constants are per 8 samples"""
        atk_ms, rel_ms = atk_ms / div, rel_ms / div
        k = knee_db / LOG2DB
        slope = 1 - 1 / ratio if ratio < 1000 else 1.0
        vals = dict(thr=round(thr_db / LOG2DB * Q24), khalf=round(k / 2 * Q24),
                    inv2k=round(Q24 / (2 * k)) if k > 0 else 0, slope=round(slope * Q30),
                    atk=onepole(atk_ms, self.fs), rel=onepole(rel_ms, self.fs), makeup=round(makeup_db / LOG2DB * Q24))
        on = self.flags(ch)
        self.wr(ch, 'thr', *[vals[n] for n in ('thr', 'khalf', 'inv2k', 'slope', 'atk', 'rel', 'makeup')])
        if not on & F['comp']:
            self.wr(ch, 'g', 0, 0)
        self.set_flags(ch, (on & ~F['comp_div']) | F['comp'] | (F['comp_div'] if div > 1 else 0))

    def _amp_wr(self, name, *vals):
        self.lab.poke(self.base + self.amp0 + AMP_FIELDS[name], struct.pack(f'<{len(vals)}i', *vals), 4)

    def amp(self, ch, voice='crunch', gain_db=None, master_db=None, pres_db=None, hard=None, tweaks=None):
        """Guitar amp on one input (one per box): replaces tube and comp there; low/peak/high become its tone stack.
        tweaks: a tone preset's own amp insides over the voice's (tight, pre, res, b1, b2, split, lp1, lp2, hard)."""
        if not self.amp0:
            raise SystemExit('this firmware has no amp block (needs strip v3)')
        v = dict(AMP_VOICES[voice])
        v.update({k: x for k, x in (tweaks or {}).items() if k in AMP_TWEAKS})
        gain = v['gain'] if gain_db is None else gain_db
        if not 0 <= gain <= 46:
            raise SystemExit('amp gain 0..46 dB')
        master = v['master'] if master_db is None else master_db
        fs = self.fs
        bq = [rbj('lpf', fs, v['pre'], q=0.7071), rbj('hpf', fs, v['tight'], q=0.7071),
              rbj('hpf', fs, v['res'], q=1.3), rbj('peak', fs, 2600.0, v['pres'] if pres_db is None else pres_db, 1.1),
              rbj('lpf', fs, v.get('lp1', 5200.0), q=0.8), rbj('lpf', fs, v.get('lp2', 7000.0), q=0.6)]
        on = self.flags(ch)
        fresh = not on & F['amp']
        for i, c in enumerate(bq):
            self.coefs(ch, NBQ + i, q30(c), reset=fresh)
        g1, g2 = stage_split(gain, v.get('split', 0.4))
        curve = lambda b: b - b ** 3 / 3
        self._amp_wr('d1', db2q27(g1), round(v['b1'] * Q27), round(curve(v['b1']) * Q27),
                     db2q27(g2), round(v['b2'] * Q27), round(curve(v['b2']) * Q27), db2q27(master))
        self._amp_wr('ch', ch)
        hard = v.get('hard', False) if hard is None else hard
        self.set_flags(ch, ((on | F['amp']) & ~F['tube'] & ~F['comp'] & ~F['amp_hard']) | (F['amp_hard'] if hard else 0))

    def fx(self, ch, echo_ms=0.0, echo_fb=0.0, echo_mix=0.0, echo_tone=5000.0, rev_mix=0.0, rev_size=0.5, rev_damp=0.5):
        """echo + reverb on one input (one per box), after its strip/amp. Mixes 0-1, feedback 0-0.9, size/damp 0-1.
        The dry sound is untouched and on time; only the repeats and the tail come later."""
        base = getattr(self, '_fx', None)
        if base is None:
            base = self._fx = self.lab.fx_addr()
        if not base or struct.unpack('<I', self.lab.peek(base, 4, 4))[0] != FX_MAGIC:
            raise SystemExit('this firmware has no echo/reverb (needs strip10)')
        fs = self.fs
        q = lambda v: round(max(-15.9, min(15.9, v)) * Q27)
        dlen = min(FX_DMAX, max(1, round(echo_ms * fs / 1000))) if echo_mix > 0 and echo_ms > 0 else 0
        ddamp = 1 - math.exp(-2 * math.pi * min(echo_tone, fs * 0.45) / fs)
        k = fs / 44100
        words = [dlen, q(min(0.9, echo_fb)), q(echo_mix), q(ddamp), q(2.0 * rev_mix), q(0.7 + 0.28 * rev_size),
                 q(1 - 0.4 * rev_damp)] + [round(n * k) for n in (1116, 1188, 1277, 1356)] + [round(n * k) for n in (556, 441)]
        self.lab.poke(base + 4, struct.pack('<7i6I', *words), 4)
        for c in range(self.nch):
            v = self.flags(c)
            want = (v | F['fx']) if c == ch and (dlen or rev_mix > 0) else (v & ~F['fx'])
            if want != v:
                self.set_flags(c, want)

    def gate(self, ch, thr_db=-45.0, hyst_db=6.0, hold_ms=60.0, att_ms=0.3, rel_ms=80.0):
        """noise gate on one input: listens to the input as it comes in, opens on the same sample (no lookahead, no
        delay), shuts after hold + release below thr - hyst. Timing is shared by all inputs."""
        base = getattr(self, '_gate', None)
        if base is None:
            base = self._gate = self.lab.gate_addr()
        if not base or struct.unpack('<I', self.lab.peek(base, 4, 4))[0] != GATE_MAGIC:
            raise SystemExit('this firmware has no noise gate (needs strip10)')
        fs = self.fs
        lin = lambda db: round(10 ** (db / 20) * Q27)
        step = lambda ms: round((1 - math.exp(-1000.0 / (max(0.05, ms) * fs))) * Q30)
        self.lab.poke(base + 4, struct.pack('<3i', round(hold_ms * fs / 1000), step(att_ms), step(rel_ms)), 4)
        self.lab.poke(base + 16 + 24 * ch, struct.pack('<2i', lin(thr_db), lin(thr_db - hyst_db)), 4)
        self.set_flags(ch, self.flags(ch) | F['gate'])

    def gate_off(self, ch):
        self.set_flags(ch, self.flags(ch) & ~F['gate'])

    def fx_off(self):
        for c in range(self.nch):
            v = self.flags(c)
            if v & F['fx']:
                self.set_flags(c, v & ~F['fx'])

    def amp_off(self, ch):
        self.set_flags(ch, self.flags(ch) & ~F['amp'] & ~F['amp_hard'])
        self._amp_wr('ch', -1)

    def off(self, ch, names):
        for c in (range(self.nch) if ch is None else [ch]):
            v = self.flags(c)
            for n in names:
                v &= ~F[n]
                if n in ('peak', 'high'):
                    v &= ~F['slim_' + n]
                elif n == 'comp':
                    v &= ~F['comp_div']
            self.set_flags(c, v)

    def gr(self, ch, reset=True):
        g, gmax = self.rd(ch, 'g'), self.rd(ch, 'grmax')
        if reset:
            self.wr(ch, 'grmax', 0)
        return g / Q24 * LOG2DB, gmax / Q24 * LOG2DB

    def status(self):
        clips = struct.unpack('<I', self.lab.peek(self.base + H_CLIPS, 4, 4))[0]
        print(f'strip v2 at {self.base:#x}, output limiter hits {clips}')
        anyon = False
        for ch in range(self.nch):
            fl = self.flags(ch)
            if not fl:
                continue
            anyon = True
            parts = [n for n in ('gain', 'hpf', 'tube', 'low', 'peak', 'high', 'comp') if fl & F[n]]
            line = f'  in {ch + 1}: ' + ' '.join(parts)
            if fl & F['gain']:
                line += f'  [in {20 * math.log10(self.rd(ch, "gin") / Q27 + 1e-12):+.1f} / out {20 * math.log10(self.rd(ch, "gout") / Q27 + 1e-12):+.1f} dB]'
            if fl & F['comp']:
                g, gmax = self.gr(ch, reset=False)
                line += f'  [GR {g:.1f} dB, max {gmax:.1f}]'
            print(line)
        if not anyon:
            print('  all off (stock sound)')


def strip_fs(lab, fs):
    if fs:
        return fs
    per = struct.unpack('<I', lab.peek(VARS + 8, 4, 4))[0]
    if not per:
        raise SystemExit('meter period is 0: is the unit armed and running 44.1/48/88.2 kHz? (or pass --fs)')
    est = CCLK / per
    fs = min((44100, 48000, 88200), key=lambda r: abs(r - est))
    if abs(fs - est) > 0.03 * fs:
        raise SystemExit(f'meter says {est:.0f} Hz: the strip runs only at 44.1/48/88.2k (or pass --fs)')
    return fs


def strip_selftest(lab):
    import os
    import random
    syms = {}
    nm = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'patches', 'lab1.nm')
    for line in open(nm):
        p_ = line.split()
        if len(p_) == 3:
            syms[p_[2]] = int(p_[0], 16)
    rnd = random.Random(1)
    cases = [(1 << 29, 1 << 30), (-(1 << 31) + 4, (1 << 30) - 1), (-(1 << 31) + 4, -(1 << 31) + 1),
             (123456789, -987654321), (-1, 1), (-1, -1), (3, -3), (0x7fffffff, 0x7fffffff)]
    cases += [(rnd.randrange(-(1 << 31), 1 << 31), rnd.randrange(-(1 << 31), 1 << 31)) for _ in range(30)]
    for name, sh in (('_strip_mul', 30), ('_strip_mul27', 27)):
        bad = 0
        for a_, b_ in cases:
            want = max(-(1 << 31), min((1 << 31) - 1, (a_ * b_) >> sh))
            r0, _ = lab.call(syms[name], a_ & 0xFFFFFFFF, b_ & 0xFFFFFFFF, 0)
            got = r0 - (1 << 32) if r0 & 0x80000000 else r0
            if got != want:
                bad += 1
                print(f'  {name} {a_} * {b_}: got {got}, want {want}')
        print(f'{name}: {len(cases) - bad}/{len(cases)} exact')


def bq_model(c, st, x):
    """Exact model of _bq_run (lab1.S) / bq_run (strip.c host build). c: stored Q30 [b0 b1 b2 -a1 -a2],
    st: [x1, x2, y1, y2, e] (updated in place). Returns y."""
    a = [x, st[0], st[1], st[2], st[3]]
    ll = mid = hh = 0
    for ai, ci in zip(a, c):
        aL, bL, aH, bH = ai & 0xFFFF, ci & 0xFFFF, ai >> 16, ci >> 16
        ll += aL * bL
        mid += aH * bL + bH * aL
        hh += aH * bH
    acc = (ll >> 16) + mid + st[4]
    e = acc & 0x3FFF
    y = max(-(1 << 31), min((1 << 31) - 1, (hh << 2) + (acc >> 14)))
    y = max(-(1 << 30), min((1 << 30) - 1, y))
    st[:] = [x, st[0], y, st[2], e]
    return y


def strip_bqtest(lab, n=200):
    """Run the unit's _bq_run sample by sample on a scratch biquad in the upload area; compare bit-exact with bq_model."""
    import os
    import random
    syms = {}
    nm = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'patches', 'lab1.nm')
    for line in open(nm):
        p_ = line.split()
        if len(p_) == 3:
            syms[p_[2]] = int(p_[0], 16)
    fn = syms['_bq_run']
    area = 0x3E0000                     # scratch, away from the page blob at 0x3B0000
    rnd = random.Random(7)
    worst = 0
    for name, c in (('hpf 100', q30(rbj('hpf', 44100, 100))), ('peak 1k +12', q30(rbj('peak', 44100, 1000, 12, 2))),
                    ('low 60 +12', q30(rbj('low', 44100, 60, 12)))):
        st = [0, 0, 0, 0, 0]
        lab.poke(area, struct.pack('<5i5i', *c, 0, 0, 0, 0, 0), 4)
        bad, cyc_min = 0, 1 << 30
        for i in range(n):
            if i < n // 2:
                x = round(0.9 * (1 << 27) * math.sin(2 * math.pi * 997 * i / 44100))
            else:
                x = rnd.choice([rnd.randrange(-(1 << 31), 1 << 31), rnd.randrange(-(1 << 27), 1 << 27), 0, (1 << 31) - 1, -(1 << 31)])
            want = bq_model(c, st, x)
            r0, cyc = lab.call(fn, area, x & 0xFFFFFFFF, 0)
            got = r0 - (1 << 32) if r0 & 0x80000000 else r0
            cyc_min = min(cyc_min, cyc)
            if got != want:
                bad += 1
                if bad <= 3:
                    print(f'  {name} #{i}: x {x} got {got} want {want}')
        unit_st = list(struct.unpack('<5i', lab.peek(area + 20, 20, 4)))
        unit_st[4] &= 0xFFFFFFFF
        ok_st = unit_st == st
        worst = max(worst, bad)
        print(f'{name}: {n - bad}/{n} samples bit-exact, state {"matches" if ok_st else f"DIFFERS {unit_st} vs {st}"}, '
              f'call {cyc_min} cycles incl. lab overhead')
    return worst


# ---- STRIP page, live (patches/page.c blob at 0x3B0000 hijacks screen 18 = AUTO-SENS setup) ----
PAGE_BASE = 0x3B0000
PAGE_SLOTS = [(0x402434, '_pg_create', 0x318704), (0x40243c, '_pg_destroy', 0x318824),
              (0x402444, '_pg_key', 0x3188e4), (0x40244c, '_pg_keyup', 0x318af6),
              (0x40245c, '_pg_draw', 0x31883c), (0x402484, '_pg_enc', 0x318894),
              # screen 2 = Channel page -> Rosetta Channel page
              (0x401554, '_chp_create', 0x310418), (0x40155c, '_pg_destroy', 0x310620),
              (0x401564, '_chp_key', 0x31076c), (0x40156c, '_chp_keyup', 0x30fb10),
              (0x40157c, '_chp_draw', 0x3106a0), (0x4015a4, '_chp_enc', 0x310958)]
CUR_SCREEN = 0x419a5e


def page_cmd(lab, a):
    import os
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'patches')
    cur = struct.unpack('<h', lab.peek(CUR_SCREEN, 2, 2))[0]
    if cur in (2, 18):
        raise SystemExit(f'the unit is on screen {cur} (Channel/STRIP) right now: go back to HOME first (DISPLAY), then retry')
    if a and a[0] == 'unload':
        for slot, _, stock in PAGE_SLOTS:
            lab.poke(slot, struct.pack('<I', stock), 4)
        print('screens 2 and 18 = stock Channel / AUTO-SENS again')
        return
    syms = {}
    for line in open(os.path.join(here, 'page.nm')):
        p_ = line.split()
        if len(p_) == 3:
            syms[p_[2]] = int(p_[0], 16)
    blob = open(os.path.join(here, 'page.bin'), 'rb').read()
    blob += bytes(-len(blob) % 4)
    if PAGE_BASE + len(blob) > 0x3E0000:
        raise SystemExit('page blob too big for the upload area')
    for slot, _, stock in PAGE_SLOTS:                   # stock first: never run half-uploaded code
        lab.poke(slot, struct.pack('<I', stock), 4)
    t0 = time.time()
    lab.poke(PAGE_BASE, blob, 1)
    want = sum(struct.unpack(f'<{len(blob) // 4}I', blob)) & 0xFFFFFFFF
    got = lab.sum(PAGE_BASE, len(blob))
    if got != want:
        raise SystemExit(f'upload sum mismatch ({got:#x} vs {want:#x}): not installing')
    lab.flush(PAGE_BASE, len(blob))
    for slot, name, _ in PAGE_SLOTS:
        lab.poke(slot, struct.pack('<I', syms[name]), 4)
    print(f'page: {len(blob)} B uploaded + verified in {time.time() - t0:.1f} s: screen 2 -> Rosetta Channel page, '
          f'screen 18 -> STRIP page. Undo: octa_lab.py page unload, or power-cycle.')


def strip_cmd(lab, a, fs):
    st = Strip(lab, fs)
    if not a:
        return st.status()
    cmd = a[0]
    if cmd == 'selftest':
        return strip_selftest(lab)
    if cmd == 'bqtest':
        return strip_bqtest(lab)
    if cmd == 'off':
        ch = None if a[1] == 'all' else int(a[1]) - 1
        return st.off(ch, [a[2]] if len(a) > 2 else list(F))
    ch = int(a[1]) - 1
    if not 0 <= ch < st.nch:
        raise SystemExit('input 1-8')
    v = [float(x) for x in a[2:]]
    if cmd == 'gr':
        g, gmax = st.gr(ch)
        print(f'in {ch + 1}: GR {g:.2f} dB now, {gmax:.2f} dB max since last read')
    elif cmd == 'gain':
        st.gain(ch, v[0], v[1] if len(v) > 1 else 0.0)
    elif cmd == 'hpf':
        st.eq(ch, 'hpf', rbj('hpf', st.fs, v[0], 0, v[1] if len(v) > 1 else 0.7071))
    elif cmd in ('low', 'peak', 'high'):
        st.eq(ch, cmd, rbj(cmd, st.fs, v[0], v[1], v[2] if len(v) > 2 else 0.7071))
    elif cmd == 'tube':
        st.tube(ch, v[0], v[1] if len(v) > 1 else 0.2, v[2] if len(v) > 2 else 0.0)
    elif cmd == 'comp':
        d = [-18.0, 4.0, 10.0, 150.0, 6.0, 0.0]
        d[:len(v)] = v
        st.comp(ch, *d)
    else:
        raise SystemExit(__doc__)
    st.status()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd')
    ap.add_argument('args', nargs='*')
    ap.add_argument('--unit', type=int, default=0)
    ap.add_argument('--w', type=int, default=1, choices=(1, 2, 4))
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--fs', type=int, default=0)
    o = ap.parse_args()
    if o.cmd == 'ports':
        print('in :', mido.get_input_names())
        print('out:', mido.get_output_names())
        return
    lab = Lab(o.unit, o.force)
    h = lambda s: int(s, 16)  # noqa: E731
    a = o.args
    if o.cmd == 'ping':
        ver, vars_, area = lab.ping()
        print(f'Rosetta lab v{ver >> 8}.{ver & 0xFF}, vars {vars_:#x}, code area {area:#x}')
    elif o.cmd == 'peek':
        hexdump(h(a[0]), lab.peek(h(a[0]), int(a[1], 0) if len(a) > 1 else 64, o.w))
    elif o.cmd == 'poke':
        lab.poke(h(a[0]), bytes.fromhex(a[1]), o.w)
    elif o.cmd == 'dump':
        open(a[2], 'wb').write(lab.peek(h(a[0]), int(a[1], 0), o.w))
    elif o.cmd == 'load':
        data = open(a[1], 'rb').read()
        data += bytes(-len(data) % 4)
        lab.poke(h(a[0]), data, 1)
        want = sum(struct.unpack(f'<{len(data) // 4}I', data)) & 0xFFFFFFFF
        got = lab.sum(h(a[0]), len(data))
        print(f'loaded {len(data)} B, sum {got:#x}', 'OK' if got == want else f'MISMATCH (want {want:#x})')
    elif o.cmd == 'sum':
        print(f'{lab.sum(h(a[0]), int(a[1], 0)):#010x}')
    elif o.cmd == 'fill':
        lab.fill(h(a[0]), int(a[1], 0), h(a[2]))
    elif o.cmd == 'check':
        bad, first = lab.check(h(a[0]), int(a[1], 0), h(a[2]))
        print(f'{bad} words differ' + (f', first at {first:#x}' if bad else ''))
    elif o.cmd == 'call':
        regs = [h(x) for x in a[1:4]] + [0] * (3 - len(a[1:4]))
        r0, cyc = lab.call(h(a[0]), *regs)
        print(f'R0 = {r0:#x}  ({cyc} cycles)')
    elif o.cmd == 'meter' and a and a[0] in ('show', 'hide'):
        f = struct.unpack('<I', lab.peek(VARS + 0x14, 4, 4))[0]
        lab.poke(VARS + 0x14, struct.pack('<I', (f | 2) if a[0] == 'show' else (f & ~2)), 4)
    elif o.cmd == 'meter':
        v = lab.peek(VARS, 0x18, 4)
        armed, last, per, bmax, frames, flags = struct.unpack('<6I', v)
        print(f'busy max {bmax} / period {per} cycles = {100 * bmax / max(per, 1):.1f}% '
              f'(CCLK ~ {per * 48000 / 1e6:.0f} MHz if 48 kHz), frames {frames}, armed {armed == 0x52535441}')
    elif o.cmd == 'chop':
        f = struct.unpack('<I', lab.peek(VARS + 0x14, 4, 4))[0]
        lab.poke(VARS + 0x14, struct.pack('<I', (f | 1) if a[0] == 'on' else (f & ~1)), 4)
    elif o.cmd == 'page':
        page_cmd(lab, a)
    elif o.cmd == 'strip':
        strip_cmd(lab, a, o.fs)
    elif o.cmd == 'memprobe':
        memprobe(lab)
    elif o.cmd == 'memcheck':
        memcheck(lab)
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main()
