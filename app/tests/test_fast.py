"""Off-hardware tests for the fast-memory bench: a fake box that behaves like the real one. python -I app/tests/test_fast.py"""
import os
import struct
import sys
import time

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, APP)
sys.path.insert(0, os.path.join(APP, '..', 'tools'))
import octa_fast as F                                 # noqa: E402
import octa_lab as L                                  # noqa: E402

F.time.sleep = lambda s: None                         # no waiting in tests
real_time = time.time
fails = []


def check(name, ok, info=''):
    print(('ok   ' if ok else 'FAIL ') + name + (f'   {info}' if info != '' and not ok else ''))
    if not ok:
        fails.append(name)


class Box:
    """memory, the strip header, and a load meter that grows with active inputs"""

    def __init__(self, per_slow=13.0, per_fast=5.0):
        self.mem, self.per_slow, self.per_fast = {}, per_slow, per_fast
        self.pokes, self.calls, self.on = [], [], 0
        self.w32(F.CBPTR, 0x3A3024)
        self.w32(F.VARS + 8, 8444)
        hdr = struct.pack('<4I', 0x53545250, 3, 8, 264) + struct.pack('<20I', *(F.EXPECT_OFF + [2244]))
        self.write(F.STRIP, hdr)
        self.sum_seq, self.copy_ret, self.bq_error, self.alive = [1234, 1234], 0, 0, 0

    def w32(self, a, v):
        self.write(a, struct.pack('<I', v))

    def write(self, a, b):
        for i, x in enumerate(b):
            self.mem[a + i] = x

    def peek(self, a, n, w=1):
        if a == F.ALIVE:                                  # the fast path's counter moves while it is the one running
            self.alive += 1 if struct.unpack('<I', bytes(self.mem[F.CBPTR + i] for i in range(4)))[0] >= 0xFFA00000 else 0
            return struct.pack('<I', self.alive)
        if a == F.VARS + 0xC:
            fast = struct.unpack('<I', bytes(self.mem[F.CBPTR + i] for i in range(4)))[0] >= 0xFFA00000
            pct = 39.0 + self.on * (self.per_fast if fast else self.per_slow)
            return struct.pack('<I', int(8444 * pct / 100))
        return bytes(self.mem.get(a + i, 0) for i in range(n))

    def poke(self, a, b, w=1):
        self.pokes.append((a, bytes(b)))
        self.write(a, b)

    def flush(self, a, n):
        pass

    def call(self, a, r0=0, r1=0, r2=0):
        self.calls.append((a, r0, r1, r2))
        if a == SYM['_fast_sum']:
            return self.sum_seq.pop(0), 0
        if a in (SYM['_bq_run'], SYM['_bq_run3'], SYM['_bq_slim'], SYM['_bq_fast'], SYM['_bq_inl']):            # the filter, as the real code should behave
            raw = self.peek(r0, 40)
            v = list(struct.unpack('<5i5i', raw))
            c, st = v[:5], v[5:]
            st[4] &= 0xFFFFFFFF
            x = r1 - (1 << 32) if r1 & 0x80000000 else r1
            y = L.bq_model(c, st, x) + self.bq_error
            self.write(r0 + 20, struct.pack('<4iI', st[0], st[1], st[2], st[3], st[4]))
            return y & 0xFFFFFFFF, 61 if a == SYM['_bq_run'] else 44
        return self.copy_ret, 0


class FakeStrip:
    def __init__(self, box):
        self.box, self.ops = box, []

    def eq(self, ch, kind, c, slim=False):
        self.ops.append(('eq', ch, kind, slim))

    def tube(self, ch, *a):
        self.ops.append(('tube', ch))

    def comp(self, ch, *a, div=1):
        self.ops.append(('comp', ch, div))
        self.box.on = max(self.box.on, ch + 1)

    def off(self, ch, blocks):
        self.ops.append(('off', ch))
        self.box.on = min(self.box.on, ch)


SYM = dict(_fast_sum=0x3B0100, _fast_copy=0x3B0200, wrap48_fast=0xFFA04900, _bq_run=0xFFA04A00, _bq_run3=0xFFA04B00, _bq_slim=0xFFA04C00, _bq_fast=0xFFA04D00, _bq_inl=0xFFA04E00)
F.symbols = lambda: dict(SYM)
F._read = lambda n: b'\x11' * 2352 if 'fast_fast' in n else b'\x22' * 748


def clock():
    """load() loops on time.time(): make each read 0.1 s later so a measurement is finite and instant"""
    now = [0.0]

    def t():
        now[0] += 0.1
        return now[0]
    F.time.time = t


clock()
fake_rbj = lambda *a: [1.0, 0, 0, 0, 0]
GOOD_MODEL = L.bq_model


def passing(box):
    """a Fast that has passed its selftest, so the ramp tests can use a simplified strip"""
    f = F.Fast(box, log=lambda s: None)
    f.selftest = lambda *a, **k: {}
    return f


def ptr(b):
    return struct.unpack('<I', b.peek(F.CBPTR, 4, 4))[0]


b = Box()
f = F.Fast(b, log=lambda s: None)
f.install()
check('copies to the right place', (SYM['_fast_copy'], F.L1_BASE, F.IMG_SRC, 2352) in b.calls, b.calls)
check('uploads the code first', [p[0] for p in b.pokes[:2]] == [F.UP_BASE, F.IMG_SRC])
check('installing alone changes no audio', ptr(b) == 0x3A3024)
f.on()
check('on: one pointer goes to fast memory', ptr(b) == SYM['wrap48_fast'])
f.off()
check('off: exact old pointer back', ptr(b) == 0x3A3024)
check('off writes only that word', b.pokes[-1][0] == F.CBPTR and len(b.pokes[-1][1]) == 4)
f2 = F.Fast(Box(), log=lambda s: None)
try:
    f2.on()
    check('on refuses before install', False)
except F.Refused:
    check('on refuses before install', True)


def refused(name, setup, want):
    b = Box()
    setup(b)
    try:
        F.Fast(b, log=lambda s: None).install()
        check(name, False, 'installed anyway')
    except F.Refused as e:
        check(name, want in str(e), str(e))
    check(name + ': nothing copied', not [c for c in b.calls if c[0] == SYM['_fast_copy']])
    check(name + ': audio untouched', ptr(b) == 0x3A3024 or setup is pointer_elsewhere)


def pointer_elsewhere(b):
    b.w32(F.CBPTR, 0xFFA08054)


def odd_layout(b):
    b.w32(F.STRIP + 12, 200)


def other_offsets(b):
    b.w32(F.STRIP + 16 + 4 * 6, 999)


def busy_memory(b):
    b.sum_seq = [1234, 5678]


def unreadable(b):
    b.sum_seq = [0xFFFFFFFE, 0xFFFFFFFE]


refused('different strip layout', odd_layout, 'differs')
refused('moved fields', other_offsets, 'differs')
refused('audio slot not ours', pointer_elsewhere, 'not our')
refused('target memory changing', busy_memory, 'changing')
refused('target memory unreadable', unreadable, 'changing')
b = Box()
b.copy_ret = 40
try:
    F.Fast(b, log=lambda s: None).install()
    check('failed verify is reported', False)
except F.Refused as e:
    check('failed verify is reported', '40' in str(e))
check('...and audio stays on the old path', ptr(b) == 0x3A3024)

b = Box()
f = F.Fast(b, log=lambda s: None)
logs = []
try:
    f.selftest(L.bq_model, L.q30, L.rbj, 44100.0)
    check('selftest refuses before install', False)
except F.Refused:
    check('selftest refuses before install', True)
f.install()
best = f.selftest(L.bq_model, L.q30, L.rbj, 44100.0, logs.append)
check('selftest passes on correct code', best['_bq_run'] == 61 and best['_bq_run3'] == 44 and '_bq_slim' in best, best)
check('install sets the SLIM comp state: staggered, unity gain', struct.unpack('<iii', b.peek(F.SLIM_STATE + 12 * 5, 12)) == (0, 5, 1 << 27))
check('...and reports the cost', 'bit-exact' in logs[0] and '61' in logs[0], logs)
b = Box()
b.bq_error = 1
f = F.Fast(b, log=lambda s: None)
f.install()
try:
    f.selftest(L.bq_model, L.q30, L.rbj, 44100.0)
    check('one wrong bit is refused', False)
except F.Refused as e:
    check('one wrong bit is refused', 'differs' in str(e), str(e))
b = Box()
b.bq_error = 1
s = FakeStrip(b)
try:
    F.bench(b, s, fake_rbj, 44100.0, L.bq_model, L.q30, log=lambda x: None)
    check('bench stops on a failed selftest', False)
except F.Refused:
    check('bench stops on a failed selftest', not s.ops and ptr(b) == 0x3A3024, s.ops)
except TypeError as e:
    check('bench stops on a failed selftest', False, e)

# the ramp, the limit, the cleanup
b = Box(per_slow=13.0, per_fast=5.0)
s = FakeStrip(b)
logs = []
r = F.bench(b, s, fake_rbj, 44100.0, GOOD_MODEL, L.q30, log=logs.append, fast=passing(b))
slow = [x for x in r if x[0] == 'slow']
fastr = [x for x in r if x[0] == 'FAT']
check('slow path never goes past 2 inputs', max(x[1] for x in slow) == 2, slow)
check('slow numbers are the real meter', abs(slow[2][2] - 65) < 0.5, slow)
slimr = [x for x in r if x[0] == 'SLIM']
check('FAT ramps to all 8', [x[1] for x in fastr] == [0, 1, 2, 4, 6, 8], fastr)
check('SLIM ramps to all 8 as well', [x[1] for x in slimr] == [0, 1, 2, 4, 6, 8], slimr)
check('SLIM asks for the cheap blocks', ('eq', 0, 'peak', True) in s.ops and ('eq', 0, 'high', True) in s.ops and ('comp', 0, 8) in s.ops)
check('FAT asks for the full ones', ('eq', 0, 'peak', False) in s.ops and ('comp', 0, 1) in s.ops)
check('numbers come while the fast path is on', abs(fastr[-1][2] - 79) < 0.5, fastr)
check('ends back on the old path', ptr(b) == 0x3A3024)
check('ends with every strip off', b.on == 0)
ops = [o[0] for o in s.ops]
check('every block is on in the test', all(('eq', 0, k, False) in s.ops for k in ('hpf', 'low', 'peak', 'high')) and ('tube', 0) in s.ops)

b = Box(per_slow=13.0, per_fast=12.0)
s = FakeStrip(b)
logs = []
r = F.bench(b, s, fake_rbj, 44100.0, GOOD_MODEL, L.q30, log=logs.append, fast=passing(b))
fastr = [x for x in r if x[0] == 'FAT']
check('stops at the limit', fastr[-1][3] >= F.LIMIT and fastr[-1][1] == 4, fastr)
check('...never tries more after that', not [x for x in fastr if x[1] > 4], fastr)
check('...says so', any('limit' in x for x in logs), logs)
check('...and cleans up', ptr(b) == 0x3A3024 and b.on == 0)


class Crash(FakeStrip):
    def comp(self, ch, *a, div=1):
        if ch == 3:
            raise SystemExit('the box stopped answering')
        super().comp(ch, *a, div=div)


b = Box()
s = Crash(b)
try:
    F.bench(b, s, fake_rbj, 44100.0, GOOD_MODEL, L.q30, log=lambda x: None, fast=passing(b))
    check('an error comes back to the caller', False)
except SystemExit:
    check('an error comes back to the caller', True)
check('an error mid-test still restores the old path', ptr(b) == 0x3A3024)
check('an error mid-test still turns strips off', [o for o in s.ops if o == ('off', 0)] and b.on == 0, b.on)

b = Box(per_slow=13.0, per_fast=5.0)
s = FakeStrip(b)
logs = []
prof = F.profile(b, s, fake_rbj, 44100.0, GOOD_MODEL, L.q30, log=logs.append, fast=passing(b))
check('profile covers every block on every path', {k[0] for k in prof} == {'slow', 'FAT', 'SLIM'} and len(prof) == 12, prof)
check('profile ends on the old path with strips off', ptr(b) == 0x3A3024 and b.on == 0)


class Stuck(Box):
    def peek(self, a, n, w=1):
        if a == F.ALIVE:
            return struct.pack('<I', 7)
        return super().peek(a, n, w)


b = Stuck()
s = FakeStrip(b)
try:
    F.profile(b, s, fake_rbj, 44100.0, GOOD_MODEL, L.q30, log=lambda x: None, fast=passing(b))
    check('a fast path that is not really running is caught', False)
except F.Refused as e:
    check('a fast path that is not really running is caught', 'not running' in str(e), str(e))
check('...and audio is back on the old path', ptr(b) == 0x3A3024)

print('\n%s' % ('ALL PASS' if not fails else 'FAILED: ' + ', '.join(fails)))
sys.exit(1 if fails else 0)
