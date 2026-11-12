"""Build a Rosetta main.bin = splash + an assembled DSP patch (patches/NAME.S, assembled by patches/asm.py).

usage: python -I tools/build_dsp.py stock/main.bin art/rosetta_splash_v4_128x64.png patches/dsp1 OUT_main.bin

Safety checks (refuses to write on any failure):
  - every hook section replaces exactly the expected stock bytes (HOOKS below);
  - code sections stay inside the dead overlay body 0x30de0c-0x30dfbb (gated by [0x4094e4]/[0x4094e8] == 0);
  - the only .text differences vs stock are the splash patches, the hooks and the overlay body;
  - runtime data (init table) differs from stock only in the splash bitmap.
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_splash_main as bsm  # noqa: E402

BASE = 0x300100
OVERLAY = (0x30de0c, 0x30dfbc)          # dead body of 0x30ddc8 (both gates 0, never written)
HOOKS = {                                # section: (address, stock bytes)
    'h_arm': (0x31bd7a, bytes.fromhex('ffe33191')),   # CALL 0x30dfdc  (mode 0, after the key scan)
    'h_ui': (0x359a16, bytes.fromhex('fde3d9a1')),    # CALL 0x30ddc8  (50 ms UI tick)
    'h_gb': (0x30de0c, bytes.fromhex('28e4')),        # gate-B target
    'h_ga': (0x30de96, bytes.fromhex('2d82')),        # gate-A target
    'h_midi': (0x36949a, bytes.fromhex('f1a37860')),  # R1 = [SP+0x3c]; R0 = 0xf  (USB-MIDI dispatcher)
    'h_save': (0x31d1cc, bytes.fromhex('02e3104b')),  # CALL 0x3667ec  (close, in Roland's settings save 0x31d160)
}


APPEND_ALIGN = 0x100


def apply(main, patch):
    """returns (new main, allowed .text offsets, table end offset). Sections at/after the image end are appended."""
    m = bytearray(main)
    table_end = len(main)
    lo, hi = OVERLAY[0] - BASE, OVERLAY[1] - BASE
    m[lo:hi] = bytes(hi - lo)                          # clear the dead overlay body (NOPs)
    allowed = set(range(lo, hi))
    for line in open(patch + '.sec'):
        sec, vma, size = line.split()
        sec, vma, size = sec[1:], int(vma, 16), int(size, 16)
        data = open(f'{patch}_{sec}.bin', 'rb').read()
        assert len(data) == size, sec
        off = vma - BASE
        if sec in HOOKS:
            a, stock = HOOKS[sec]
            assert vma == a and len(data) == len(stock), f'{sec}: wrong address/size'
            assert main[off:off + len(stock)] == stock, f'{sec}: stock bytes mismatch'
            allowed |= set(range(off, off + len(data)))
            m[off:off + size] = data
        elif off >= table_end:
            assert off % APPEND_ALIGN == 0 and off - table_end < APPEND_ALIGN, f'{sec}: append address {vma:#x}'
            assert vma + size <= 0x3F0000, f'{sec}: appended code runs into the upload area'
            m += bytes(off - len(m)) + data
        else:
            assert OVERLAY[0] <= vma and vma + size <= OVERLAY[1], f'{sec} outside the dead overlay'
            m[off:off + size] = data
    return bytes(m), allowed, table_end


def main(a):
    stock = open(a[0], 'rb').read()
    spl = bsm.build(stock, bsm.splash_bytes(a[1]))
    new, allowed, tend = apply(spl, a[2])
    allowed |= {o + j for n, (o, old, rep) in bsm.PATCHES.items() for j in range(len(old))}
    tdiff = [i for i in range(bsm.TEXT_END) if stock[i] != new[i]]
    assert set(tdiff) <= allowed, [hex(x + BASE) for x in tdiff if x not in allowed][:8]
    m0, m1 = bsm.run(bsm.parse(stock)), bsm.run(bsm.parse(new, tend))
    diff = [x for x in set(m0) | set(m1) if m0.get(x) != m1.get(x)]
    assert all(bsm.BMP_ADDR <= x < bsm.BMP_ADDR + 0x400 for x in diff), 'runtime data changed outside the bitmap'
    open(a[3], 'wb').write(new)
    print(f'{a[3]}: .text bytes changed {len(tdiff)} (all splash/hooks/overlay), runtime data diff only in the bitmap'
          + (f', {len(new) - tend} bytes appended at {tend + BASE:#x}' if len(new) > tend else ''))


if __name__ == '__main__':
    main(sys.argv[1:])
