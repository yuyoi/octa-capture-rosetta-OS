"""Build a Rosetta main.bin for the OCTA-CAPTURE with a custom 128x64 boot splash.

usage: python -I build_splash_main.py stock/main.bin splash.png out/main_hex.bin [--keep-text] [--keep-bar-pos]

splash.png: 128x64, white = ink (what tools/splash.py writes).
What it does (see re/BOOT_SCREEN_PLAN.md):
  A  replace bitmap resource 0xf5 (runtime 0x433c20, 1024 B) by rebuilding the VisualDSP init table:
     records 960-964 (2 copy + 3 pattern-fill) -> one copy record + 1024 B; record count 0x594 -> 0x590
  B1 NOP the "V1.51(017B)" DrawString call at 0x3199d6         (unless --keep-text)
  B2 NOP the " Starting..." mode-string DrawString at 0x3199e2  (unless --keep-text)
  C  move the 104x8 child widget from y=34 to y=56             (unless --keep-bar-pos)
Then it emulates the whole init table for stock and patched images and asserts the ONLY runtime
difference is inside 0x433c20-0x43401f, and that every .text change is exactly the listed patch bytes.
"""
import struct
import sys

import numpy as np
from PIL import Image

TEXT_END = 0x6e56c          # file offset where .text ends (pointer to init table follows)
TABLE_PTR = 0x6e56c
COUNT_OFF = 0x6e574
BMP_ADDR = 0x433c20
BMP_RECS = [0x433c20, 0x433c80, 0x433d3a, 0x433d60, 0x433e20]

PATCHES = {  # name: (file offset, stock bytes, new bytes)
    'B1_no_version_text': (0x198d6, bytes.fromhex('01e3c5a8'), bytes(4)),
    'B2_no_starting_text': (0x198e2, bytes.fromhex('01e3bfa8'), bytes(4)),
    'C_bar_to_y56': (0x19794, bytes.fromhex('1261'), bytes.fromhex('c261')),
}


def parse(d):
    assert struct.unpack_from('<I', d, TABLE_PTR)[0] == 0x36e670
    p = 0x6e570
    cnt = struct.unpack_from('<I', d, p + 4)[0]
    p += 8
    recs = []
    for _ in range(cnt):
        dst, size, fl, pl = struct.unpack_from('<4I', d, p)
        k = (fl & 0x1c0) >> 6
        assert fl & 0x3c == 0 and fl & 3 == 2 and k in (0, 1, 3), hex(p)
        h = p
        p += 16
        n = 0 if k == 1 else (size if k == 0 else pl)
        recs.append((h, dst, size, fl, pl, k, d[p:p + n]))
        p += (n + 3) & ~3
    assert p == len(d), f'table does not end at EOF: {p:#x} vs {len(d):#x}'
    return recs


def run(recs):
    mem = {}
    for h, dst, size, fl, pl, k, data in recs:
        if k == 0:
            b = data
        elif k == 1:
            b = bytes(size)
        else:
            b = (data * (size // pl + 1))[:size]
        for i, x in enumerate(b):
            mem[dst + i] = x
    return mem


def splash_bytes(png):
    im = Image.open(png).convert('L')
    assert im.size == (128, 64), im.size
    ink = np.asarray(im) > 127                       # white = ink in the PNG
    data = np.packbits(~ink, axis=1).tobytes()        # LCD resource: ink = 0, bg = 1, MSB = left
    assert len(data) == 1024
    return data


def build(stock, img, keep_text=False, keep_bar=False):
    recs = parse(stock)
    dsts = [r[1] for r in recs]
    i0 = dsts.index(BMP_ADDR)
    assert dsts[i0:i0 + 5] == BMP_RECS, 'stock bitmap records not where expected'
    h0, h1 = recs[i0][0], recs[i0 + 5][0]
    new = bytearray(stock[:h0]) + struct.pack('<4I', BMP_ADDR, 0x400, 2, 0) + img + stock[h1:]
    struct.pack_into('<I', new, COUNT_OFF, len(recs) - 4)
    applied = []
    for name, (off, old, rep) in PATCHES.items():
        if keep_text and name.startswith('B'):
            continue
        if keep_bar and name.startswith('C'):
            continue
        assert new[off:off + len(old)] == old, f'{name}: stock bytes mismatch'
        new[off:off + len(old)] = rep
        applied.append(name)
    new = bytes(new)
    # --- checks ---
    m0, m1 = run(recs), run(parse(new))
    diff = [a for a in set(m0) | set(m1) if m0.get(a) != m1.get(a)]
    assert all(BMP_ADDR <= a < BMP_ADDR + 0x400 for a in diff), 'runtime data changed outside the bitmap!'
    assert set(m0) == set(m1), 'runtime address set changed'
    assert bytes(m1[BMP_ADDR + i] for i in range(1024)) == img
    tdiff = [i for i in range(TEXT_END) if stock[i] != new[i]]
    allowed = {o + j for n, (o, old, rep) in PATCHES.items() if n in applied for j in range(len(old))}
    assert set(tdiff) <= allowed, f'unexpected .text changes at {[hex(x) for x in tdiff if x not in allowed][:8]}'
    print(f'main.bin {len(stock):#x} -> {len(new):#x}; runtime diffs {len(diff)} bytes, all in bitmap 0xf5; '
          f'.text bytes changed {len(tdiff)}; patches: {", ".join(applied) or "none"}')
    return new


if __name__ == '__main__':
    a = sys.argv[1:]
    stock = open(a[0], 'rb').read()
    out = build(stock, splash_bytes(a[1]), '--keep-text' in a, '--keep-bar-pos' in a)
    open(a[2], 'wb').write(out)
    print('wrote', a[2])
