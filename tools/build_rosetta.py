"""Build the Rosetta OCTA-CAPTURE update file from Roland's own v1.51 download.

usage: python build_rosetta.py octa_sys_v151.zip [OUT_UpdOcta.dat] [--splash art/xxx_128x64.png]

  octa_sys_v151.zip  Roland's system program v1.51 download (or the UpdOcta.dat inside it).
  OUT                default: UpdOcta.dat next to the input (never overwrites Roland's file).
  --splash           any 128x64 PNG, white = ink. Default: art/rosetta_splash_v4_128x64.png (hardware-tested).

No Roland code is in this repo: everything is built from your own copy of Roland's file.
The default build is byte-identical to the one tested on a real unit (md5 printed at the end).
"""
import hashlib
import io
import os
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
import build_splash_main as bsm  # noqa: E402
import octa_fw  # noqa: E402

STOCK_MD5 = '9fdc2a497260941290d933b2b8c831fb'
DEFAULT_SPLASH = os.path.join(ROOT, 'art', 'rosetta_splash_v4_128x64.png')
TESTED = {'rosetta_splash_v4_128x64.png': '16019be5fa6577e20399bde599bdcbdd'}


def read_stock(path):
    if path.lower().endswith('.zip'):
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith('updocta.dat')]
            if not names:
                sys.exit('no UpdOcta.dat inside ' + path)
            return z.read(names[0])
    return open(path, 'rb').read()


def main(a):
    if not a or a[0] in ('-h', '--help'):
        sys.exit(__doc__)
    splash = a[a.index('--splash') + 1] if '--splash' in a else DEFAULT_SPLASH
    pos = [x for i, x in enumerate(a) if not x.startswith('--') and (i == 0 or a[i - 1] != '--splash')]
    src = pos[0]
    out = pos[1] if len(pos) > 1 else os.path.join(os.path.dirname(os.path.abspath(src)), 'UpdOcta.dat')
    stock = read_stock(src)
    md5 = hashlib.md5(stock).hexdigest()
    if md5 != STOCK_MD5:
        sys.exit(f'this is not Roland OCTA-CAPTURE system v1.51 (md5 {md5}); refusing to build')
    if os.path.abspath(out) == os.path.abspath(src):
        sys.exit('refusing to overwrite the input file')
    print('stock v1.51 OK')
    base = octa_fw.REGIONS['main']
    size = int.from_bytes(stock[base + 0x100:base + 0x104], 'big')
    main_img, _ = octa_fw.unlz(stock, base + 0x104, size)
    new_main = bsm.build(main_img, bsm.splash_bytes(splash))
    with tempfile.TemporaryDirectory() as t:
        sp, mp = os.path.join(t, 'stock.dat'), os.path.join(t, 'main.bin')
        open(sp, 'wb').write(stock)
        open(mp, 'wb').write(new_main)
        if not octa_fw.pack(sp, mp, out):
            sys.exit('check FAILED, do not flash')
    d = open(out, 'rb').read()
    diff = [i for i in range(len(d)) if d[i] != stock[i]]
    assert min(diff) >= 0x20000 and max(diff) < 0xF0000, 'bytes changed outside the main program!'
    m = hashlib.md5(d).hexdigest()
    want = TESTED.get(os.path.basename(splash))
    print(f'wrote {out}  md5 {m}' + ('' if want is None else
          ('  = the hardware-tested build' if m == want else '  (DIFFERS from the tested build!)')))


if __name__ == '__main__':
    main(sys.argv[1:])
