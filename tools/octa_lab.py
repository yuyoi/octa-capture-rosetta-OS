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
  python tools/octa_lab.py chop on|off                    record-ch1 chopper (audible test)
  python tools/octa_lab.py memprobe                       SDRAM geometry check + canaries in the free areas
  python tools/octa_lab.py memcheck                       count canary words that changed since memprobe
  python tools/octa_lab.py ports                          list MIDI ports

Options: --unit N (0 = first OCTA, 1 = second), --force (allow addresses outside the known-safe map).
Peek/poke outside mapped memory can crash the unit: then power-cycle it (nothing is written to flash).
"""
import argparse
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
    def __init__(self, unit=0, force=False):
        self.force = force
        outs = [p for p in mido.get_output_names() if 'CTRL' in p and 'OCTA' in p]
        ins = [p for p in mido.get_input_names() if 'CTRL' in p and 'OCTA' in p]
        if len(outs) <= unit or len(ins) <= unit:
            raise SystemExit(f'no OCTA-CAPTURE CTRL port #{unit}. Ports: {mido.get_output_names()}')
        self.out = mido.open_output(sorted(outs)[unit])
        self.inp = mido.open_input(sorted(ins)[unit])
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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd')
    ap.add_argument('args', nargs='*')
    ap.add_argument('--unit', type=int, default=0)
    ap.add_argument('--w', type=int, default=1, choices=(1, 2, 4))
    ap.add_argument('--force', action='store_true')
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
    elif o.cmd == 'meter':
        v = lab.peek(VARS, 0x18, 4)
        armed, last, per, bmax, frames, flags = struct.unpack('<6I', v)
        print(f'busy max {bmax} / period {per} cycles = {100 * bmax / max(per, 1):.1f}% '
              f'(CCLK ~ {per * 48000 / 1e6:.0f} MHz if 48 kHz), frames {frames}, armed {armed == 0x52535441}')
    elif o.cmd == 'chop':
        lab.poke(VARS + 0x14, struct.pack('<I', 1 if a[0] == 'on' else 0), 4)
    elif o.cmd == 'memprobe':
        memprobe(lab)
    elif o.cmd == 'memcheck':
        memcheck(lab)
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main()
