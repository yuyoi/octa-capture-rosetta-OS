"""Host test of patches/lab.c driven by the real tools/octa_lab.py Lab class (run on newpc: see run_host_test.sh)."""
import ctypes, struct, sys, types

lib = ctypes.CDLL('./liblab.so')
lib.lab_rx.argtypes = [ctypes.c_uint32]
TXCB = ctypes.CFUNCTYPE(None, ctypes.c_uint32)
sent = []
cb = TXCB(lambda w: sent.append(w))
lib.host_set_tx(cb)
lib.host_map.restype = ctypes.POINTER(ctypes.c_uint8)
lib.host_map.argtypes = [ctypes.c_uint32]


class Msg:
    def __init__(self, type, data=()):
        self.type, self.data = type, tuple(data)


consumed = []


def to_packets(data, cable):
    b = [0xF0] + list(data) + [0xF7]
    out, i = [], 0
    while i < len(b):
        k = min(3, len(b) - i)
        cin = 4 if len(b) - i > 3 else 4 + k
        w = (cable << 4) | cin
        for j in range(k):
            w |= b[i + j] << (8 + 8 * j)
        out.append(w)
        i += k
    return out


def from_packets(ws, cable):
    b = []
    for w in ws:
        assert (w >> 4) & 0xF == cable, f'reply on wrong cable: {w:#x}'
        cin = w & 0xF
        n = 3 if cin in (4, 7) else 2 if cin == 6 else 1
        b += [(w >> (8 + 8 * j)) & 0xFF for j in range(n)]
    assert b[0] == 0xF0 and b[-1] == 0xF7, b
    return b[1:-1]


class FakeOut:
    cable = 1
    def send(self, m):
        sent.clear()
        for w in to_packets(m.data, self.cable):
            consumed.append(lib.lab_rx(w))
        FakeIn.q = [Msg('sysex', from_packets(sent, self.cable))] if sent else []


class FakeIn:
    q = []
    def iter_pending(self):
        q, FakeIn.q = FakeIn.q, []
        return iter(q)


mido = types.ModuleType('mido')
mido.Message = Msg
mido.get_output_names = lambda: ['CTRL (OCTA-CAPTURE) 1']
mido.get_input_names = lambda: ['CTRL (OCTA-CAPTURE) 0']
mido.open_output = lambda n: FakeOut()
mido.open_input = lambda n: FakeIn()
sys.modules['mido'] = mido
sys.path.insert(0, '.')
import octa_lab  # noqa: E402

lab = octa_lab.Lab()
ok = 0
def check(cond, what):
    global ok
    if not cond:
        raise SystemExit('FAIL: ' + what)
    ok += 1

check(lab.ping() == (0x0101, 0x41D5E4, 0x3B0000), 'ping')
check(all(consumed), 'lab packets are swallowed')
data = bytes(range(256))
lab.poke(0x500000, data)
check(lab.peek(0x500000, 256) == data, 'poke/peek bytes across chunks')
lab.poke(0x500100, struct.pack('<4I', 1, 2, 3, 0xCAFEBABE), 4)
check(lab.peek(0x500100, 16, 4) == struct.pack('<4I', 1, 2, 3, 0xCAFEBABE), 'peek/poke width 4')
lab.poke(0xFFC00700, bytes.fromhex('3412'), 2)
check(lab.peek(0xFFC00700, 2, 2) == bytes.fromhex('3412'), 'mmr width 2')
lab.fill(0x600000, 0x10000, 0x5A5AA5A5)
check(lab.check(0x600000, 0x10000, 0x5A5AA5A5) == (0, 0xFFFFFFFF), 'fill/check clean')
lab.poke(0x608000, b'\0\0\0\0', 4)
check(lab.check(0x600000, 0x10000, 0x5A5AA5A5) == (1, 0x608000), 'check finds one changed word')
blob = bytes((i * 7) & 0xFF for i in range(1000))
lab.poke(0x3B0000, blob + bytes(-len(blob) % 4))
want = sum(struct.unpack('<250I', blob)) & 0xFFFFFFFF
check(lab.sum(0x3B0000, 1000) == want, 'sum')
check(lab.call(0x3B0000, 1, 2, 3)[0] == 6, 'call')
lab.flush(0x3B0000, 1000)
check(lib.host_flushed() == (0x3B0000 ^ (1000 << 1)), 'flush args')
# firmware-side guard: L1 code / unmapped must be refused without touching memory
lab.force = True
r = lab.xfer(octa_lab.CMD['PEEK'], octa_lab.nib(0xFFA00000, 8) + octa_lab.nib(16, 2) + octa_lab.nib(4, 1))
check(r == b'' and lib.host_faults() == 0, 'peek of L1 code refused on the unit')
lab.xfer(octa_lab.CMD['FILL'], octa_lab.nib(0x00FFFFF0, 8) + octa_lab.nib(0x100, 8) + octa_lab.nib(1, 8))
check(lib.host_faults() == 0, 'fill past 16 MB refused')
# Roland traffic must pass through untouched (not consumed), e.g. identity request and a DT1
consumed.clear(); sent.clear()
for w in to_packets([0x7E, 0x10, 0x06, 0x01], 1) + to_packets([0x41, 0x10, 0, 0, 0x4D, 0x12, 1, 2, 3, 4, 5, 0x71], 1):
    consumed.append(lib.lab_rx(w))
check(not any(consumed) and not sent, 'Roland SysEx passes through, no reply')
# cable 0 never answered (and never reaches the dispatcher anyway)
sent.clear()
for w in to_packets([0x7D, 0x4F, 1, 5], 0):
    check(lib.lab_rx(w) == 0, 'cable 0 ignored')
check(not sent, 'no reply on cable 0')
# reply goes back on cable 2 when asked on cable 2
FakeOut.cable = 2
check(lab.ping()[0] == 0x0101, 'ping on cable 2')
FakeOut.cable = 1
try:
    lab.sum(0x00BFFFFC, 4)
    check(False, 'fault must raise')
except octa_lab.LabFault as e:
    check(e.excause == 0x26 and e.addr == 0x00BFFFFC, f'fault reported: {e}')
check(lab.ping()[0] == 0x0101, 'alive after a fault')
check(lab.sum(0x500000, 64) is not None, 'normal sum after a fault')
print(f'ALL {ok} CHECKS PASSED')
