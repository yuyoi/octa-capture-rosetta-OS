"""Live A/B of the fast-memory audio path (patches/fast.S), through the lab link. Nothing here touches flash.

install()  uploads the loader and the code, checks the strip layout matches, checks the target memory is quiet, copies, verifies.
on()/off() swap one pointer: Roland's audio callback slot -> wrap48_fast, or back to the SDRAM wrap48. A power cycle undoes it.
bench()    ramps inputs up with a full, sonically neutral chain, reads the real load meter, and stops before she's overloaded.
"""
import os
import struct
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PATCHES = os.path.join(HERE, '..', 'patches')
CBPTR = 0xFF8010B4
VARS = 0x41D5E4
UP_BASE = 0x3B0000
IMG_SRC = 0x3B1000
L1_BASE = 0xFFA04900
STRIP = 0xFF8034C0
EXPECT_OFF = [132, 40, 0, 4, 8, 12, 212, 216, 220, 224, 228, 232, 236, 240, 244, 248, 252, 256, 260]
LIMIT = 80.0                            # % of a sample period: the ramp stops here
BLOCKS = ('hpf', 'low', 'peak', 'high', 'tube', 'comp')
COMP_DIV = 8
SLOW_MAX = 2                            # inputs: the SDRAM path is measured no further than this (39 % + 13 % each)
FAST_INFO = 0xFF803FF4                  # fast_boot.c: build stamp, state
ALIVE = 0xFF803FF0                      # fast.S counts every sample it runs here
SLIM_STATE = 0xFF803EA0                 # strip.h SLIM_ADDR
SCRATCH_BQ = 0x3E0000                   # one test filter, in the free SDRAM the older on-unit test already uses


class Refused(Exception):
    pass


def symbols():
    out = {}
    with open(os.path.join(PATCHES, 'fast.nm')) as f:
        for ln in f:
            p = ln.split()
            if len(p) == 3:
                out[p[2]] = int(p[0], 16)
    return out


def _read(name):
    with open(os.path.join(PATCHES, name), 'rb') as f:
        return f.read()


def rounded(c):
    """what SLIM sends: b0-b2 with their low halves zero"""
    return [((v + 0x8000) >> 16) << 16 for v in c[:3]] + list(c[3:])


class Fast:
    def __init__(self, lab, log=print):
        self.lab, self.log, self.slow_ptr, self.installed = lab, log, None, False
        self.sym = symbols()
        self.img = _read('fast_fast.bin')
        self.up = _read('fast_up.bin')

    def u32(self, a):
        return struct.unpack('<I', self.lab.peek(a, 4, 4))[0]

    def check_layout(self):
        """the fast code is built against strip.h: refuse a box whose strip block is laid out differently"""
        magic, ver, nch, size = struct.unpack('<4I', self.lab.peek(STRIP, 16, 4))
        off = list(struct.unpack('<20I', self.lab.peek(STRIP + 16, 80, 4)))
        if magic != 0x53545250 or ver not in (2, 3, 4) or nch != 8 or size != 264 or off[:19] != EXPECT_OFF:
            raise Refused(f'strip block differs from this build (ver {ver}, chan {size}, off {off[:4]})')

    def install(self):
        self.check_layout()
        ptr = self.u32(CBPTR)
        if not 0x3A3000 <= ptr < 0x3B0000:
            raise Refused(f'audio callback is not our SDRAM wrap48 ({ptr:#x})')
        self.slow_ptr = ptr
        if len(self.up) > IMG_SRC - UP_BASE:
            raise Refused('loader too big')
        for addr, data in ((UP_BASE, self.up), (IMG_SRC, self.img)):
            self.lab.poke(addr, data)
            self.lab.flush(addr, len(data))
            back = self.lab.peek(addr, len(data))
            if back != data:
                raise Refused(f'upload to {addr:#x} did not read back')
        n = len(self.img)
        a = self.lab.call(self.sym['_fast_sum'], L1_BASE, n)[0]
        time.sleep(0.5)
        b = self.lab.call(self.sym['_fast_sum'], L1_BASE, n)[0]
        if a >= 0xFFFFFFFE or a != b:
            raise Refused(f'that fast memory is changing or unreadable ({a:#x} then {b:#x}): something may be using it')
        r = self.lab.call(self.sym['_fast_copy'], L1_BASE, IMG_SRC, n)[0]
        if r:
            raise Refused(f'copy to fast memory failed, reason {r}')
        self.lab.poke(SLIM_STATE, b''.join(struct.pack('<iii', 0, ch, 1 << 27) for ch in range(8)), 4)   # staggered, unity gain
        self.installed = True
        self.log(f'fast: {n} bytes in fast memory and verified')

    def selftest(self, model, q30, rbj, fs, log=None, n=120):
        """run the code now sitting in fast memory against the exact maths model, sample by sample: one wrong bit refuses.
        Returns the fewest cycles seen per filter, the real cost with nothing in the way."""
        import math
        import random
        if not self.installed:
            raise Refused('not installed')
        rnd = random.Random(7)
        best = {}
        for sym, name, c in (('_bq_run', 'hpf', q30(rbj('hpf', fs, 100.0))), ('_bq_run', 'peak', q30(rbj('peak', fs, 1000.0, 12.0, 2.0))),
                             ('_bq_fast', 'hpf', q30(rbj('hpf', fs, 100.0))), ('_bq_fast', 'peak', q30(rbj('peak', fs, 1000.0, 12.0, 2.0))),
                             ('_bq_fast', 'low', q30(rbj('low', fs, 60.0, 12.0))), ('_bq_fast', 'high', q30(rbj('high', fs, 8000.0, 6.0))),
                             ('_bq_run', 'low', q30(rbj('low', fs, 60.0, 12.0))), ('_bq_run3', 'dc', q30(rbj('dc', fs, 10.0))),
                             ('_bq_slim', 'high', rounded(q30(rbj('high', fs, 8000.0, 3.0))))):
            self.lab.poke(SCRATCH_BQ, struct.pack('<5i5i', *c, 0, 0, 0, 0, 0), 4)
            self.lab.flush(SCRATCH_BQ, 40)
            st = [0, 0, 0, 0, 0]
            for i in range(n):
                if i < n // 2:
                    x = round(0.9 * (1 << 27) * math.sin(2 * math.pi * 997 * i / fs))
                else:
                    x = rnd.choice([rnd.randrange(-(1 << 27), 1 << 27), rnd.randrange(-(1 << 29), 1 << 29), 0,
                                    rnd.randrange(-(1 << 31), 1 << 31), (1 << 31) - 1, -(1 << 31)])
                want = model(c, st, x)
                r0, cyc = self.lab.call(self.sym[sym], SCRATCH_BQ, x & 0xFFFFFFFF, 0)
                got = r0 - (1 << 32) if r0 & 0x80000000 else r0
                if got != want:
                    raise Refused(f'{sym} on {name} differs from the model at sample {i}: {got} vs {want}')
                best[sym] = min(best.get(sym, 1 << 30), cyc)
        if log:
            log('fast selftest: bit-exact. Cycles per call, including the test harness: ' + ', '.join(f'{k} {v}' for k, v in sorted(best.items())))
        return best

    def in_firmware(self):
        """the firmware already runs this exact fast path (fast_boot.c): same build stamp, pointer on wrap48_fast, alive"""
        stamp, state = struct.unpack('<2I', self.lab.peek(FAST_INFO, 8, 4))
        blob = self.img + bytes(-len(self.img) % 4)
        mine = sum(int.from_bytes(blob[i:i + 4], 'little') for i in range(0, len(blob), 4)) & 0xFFFFFFFF
        if not state & 1 or stamp != mine or self.u32(CBPTR) != self.sym['wrap48_fast'] or not self.running():
            return False
        self.installed = True
        return True

    def on(self):
        if not self.installed:
            raise Refused('not installed')
        self.lab.poke(CBPTR, struct.pack('<I', self.sym['wrap48_fast']), 4)

    def off(self):
        if self.slow_ptr:
            self.lab.poke(CBPTR, struct.pack('<I', self.slow_ptr), 4)

    def running(self):
        """is the fast path really the one running? its sample counter must move"""
        a = self.u32(ALIVE)
        time.sleep(0.2)
        return self.u32(ALIVE) != a

    def load(self, secs=1.5):
        """(typical %, worst %) of a sample period, from the box's own busy/period counters"""
        vals = []
        end = time.time() + secs
        while time.time() < end:
            per = self.u32(VARS + 8)
            busy = self.u32(VARS + 0xC)
            if per and busy:
                vals.append(100.0 * busy / per)
            time.sleep(0.06)
        if not vals:
            raise Refused('load meter is not running')
        vals.sort()
        return vals[len(vals) // 2], vals[-1]


def neutral_chain(strip, ch, rbj, fs, slim=False):
    """every block on with settings that leave the sound alone, because the cost doesn't depend on them"""
    strip.eq(ch, 'hpf', rbj('hpf', fs, 20.0))
    strip.eq(ch, 'low', rbj('low', fs, 120.0, 0.0))
    strip.eq(ch, 'peak', rbj('peak', fs, 2500.0, 0.0, 1.0), slim=slim)
    strip.eq(ch, 'high', rbj('high', fs, 8000.0, 0.0), slim=slim)
    strip.tube(ch, 0.0, 0.0, 0.0)
    strip.comp(ch, 0.0, 1.5, 10.0, 100.0, 6.0, 0.0, div=COMP_DIV if slim else 1)


PROFILE = (('hpf', lambda s, ch, rbj, fs, slim: s.eq(ch, 'hpf', rbj('hpf', fs, 20.0))),
           ('peak', lambda s, ch, rbj, fs, slim: s.eq(ch, 'peak', rbj('peak', fs, 2500.0, 0.0, 1.0), slim=slim)),
           ('tube', lambda s, ch, rbj, fs, slim: s.tube(ch, 0.0, 0.0, 0.0)),
           ('comp', lambda s, ch, rbj, fs, slim: s.comp(ch, 0.0, 1.5, 10.0, 100.0, 6.0, 0.0, div=COMP_DIV if slim else 1)))


def profile(lab, strip, rbj, fs, model, q30, log=print, fast=None, n=4):
    """what each block costs, in cycles per input, on the old path, FAT and SLIM: one block at a time on n inputs"""
    fast = fast or Fast(lab, log)
    fast.install()
    fast.selftest(model, q30, rbj, fs, log)
    per = fast.u32(VARS + 8)
    out = {}
    try:
        for ch in range(8):
            strip.off(ch, list(BLOCKS))
        for path in ('slow', 'FAT', 'SLIM'):
            if path == 'slow':
                fast.off()
            else:
                fast.on()
                if not fast.running():
                    raise Refused('the fast path was switched on but its counter does not move: it is not running')
            base = fast.load()[0]
            for name, turn_on in PROFILE:
                for ch in range(n):
                    turn_on(strip, ch, rbj, fs, path == 'SLIM')
                typ = fast.load()[0]
                for ch in range(n):
                    strip.off(ch, list(BLOCKS))
                cyc = (typ - base) / 100.0 * per / n
                out[(path, name)] = cyc
                log(f'profile {path:4s} {name:4s}: {cyc:6.0f} cycles per input   ({typ - base:+.1f} % for {n} inputs)')
    finally:
        fast.off()
        for ch in range(8):
            strip.off(ch, list(BLOCKS))
    return out


def bench(lab, strip, rbj, fs, model, q30, log=print, fast=None):
    """check the fast code bit for bit, then ramp inputs on the SDRAM path and on the fast path. Always ends with the SDRAM
    path and every strip off; the caller puts the user's chains back."""
    fast = fast or Fast(lab, log)
    fast.install()
    fast.selftest(model, q30, rbj, fs, log)
    results = []
    on = 0

    def ramp(path, stops, slim=False):
        nonlocal on
        for n in stops:
            while on < n:
                neutral_chain(strip, on, rbj, fs, slim)
                on += 1
            typ, worst = fast.load()
            results.append((path, n, typ, worst))
            log(f'bench {path}: {n} full inputs = {typ:.1f} % typical, {worst:.1f} % worst')
            if worst >= LIMIT:
                log(f'bench {path}: stopped at the {LIMIT:.0f} % limit')
                return

    def strips_off():
        nonlocal on
        for ch in range(on):
            strip.off(ch, list(BLOCKS))
        on = 0

    try:
        for ch in range(8):
            strip.off(ch, list(BLOCKS))
        ramp('slow', range(0, SLOW_MAX + 1))
        strips_off()
        fast.on()
        if not fast.running():
            raise Refused('the fast path was switched on but its counter does not move: it is not running')
        ramp('FAT', (0, 1, 2, 4, 6, 8))
        strips_off()
        ramp('SLIM', (0, 1, 2, 4, 6, 8), slim=True)
    finally:
        fast.off()
        strips_off()
    return results
