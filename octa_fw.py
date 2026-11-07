"""OCTA-CAPTURE (UA-1010) UpdOcta.dat tool - Rosetta line.

File layout (1 MB, sys v1.51):
  0x00000-0x00FFF  booter  (raw, Blackfin, XIP from flash 0x20000000)
  0x01000-0x03FFF  loader  (raw) -- checksum: 16-bit byte sum of [0x1000,0x3FFE) at 0x3FFE (LE)
  0x04000-0x1FFFF  updater (header 0x100 + LZSS) -- byte sum of [0x4000,0x1FFFE) at 0x1FFFE
  0x20000-0xEFFFF  main    (header 0x100 + LZSS) -- byte sum of [0x20000,0xEFFFE) at 0xEFFFE
  0xF0000-0xFFFFF  0xDEAD filler
Packed region: +0x00 'EDIROL VS100 application program...' text, +0xF8 version (LE, hi=major lo=minor),
+0xFC..0xEFFFC build no. 0x017B lives at region end-4, +0x100 uncompressed size (BE32), +0x104 LZSS stream.
LZSS = Okumura: N=4096, F=18, THRESHOLD=2, ring pre-filled with ZEROS, r starts 0xFEE,
flag byte LSB-first, 1 = literal, match = 12-bit ring pos + 4-bit (len-3).
Images run from SDRAM at 0x300100. main.bin = .text, then a VisualDSP init table that builds the data at boot.
The updater only checks magic 0xAD7BD006 + file size 1 MB; the loader checks the byte sums at boot.

usage:
  python octa_fw.py unpack UpdOcta.dat OUTDIR
  python octa_fw.py pack   STOCK_UpdOcta.dat main.bin OUT_UpdOcta.dat [--version 1.51]
  python octa_fw.py check  UpdOcta.dat
"""
import os
import sys

REGIONS = {"updater": 0x4000, "main": 0x20000}
REGION_END = {"updater": 0x20000, "main": 0xF0000}
SUMS = [(0x1000, 0x4000), (0x4000, 0x20000), (0x20000, 0xF0000)]
N, F, TH = 4096, 18, 2


def unlz(src, pos, size):
    win = bytearray(N)  # Roland variant: ring pre-filled with ZEROS (not spaces)
    r = N - F
    out = bytearray()
    flags = 0
    while len(out) < size:
        flags >>= 1
        if not flags & 0x100:
            flags = src[pos] | 0xFF00
            pos += 1
        if flags & 1:
            c = src[pos]
            pos += 1
            out.append(c)
            win[r] = c
            r = (r + 1) & (N - 1)
        else:
            i = src[pos] | ((src[pos + 1] & 0xF0) << 4)
            j = (src[pos + 1] & 0x0F) + TH + 1
            pos += 2
            for k in range(j):
                c = win[(i + k) & (N - 1)]
                out.append(c)
                win[r] = c
                r = (r + 1) & (N - 1)
    return bytes(out[:size]), pos


def lz(data):
    """Greedy LZSS with hash chains; output decodes with unlz()."""
    n = len(data)
    out = bytearray()
    head = {}
    prev = [-1] * n
    i = 0
    maxdist = N - F

    def insert(p):
        if p + 3 <= n:
            key = data[p:p + 3]
            prev[p] = head.get(key, -1)
            head[key] = p

    while i < n:
        flagpos = len(out)
        out.append(0)
        flags = 0
        for bit in range(8):
            if i >= n:
                break
            best_len, best_pos = 0, 0
            if i + 3 <= n:
                cand = head.get(data[i:i + 3], -1)
                tries = 0
                lim = min(F, n - i)
                while cand >= 0 and i - cand <= maxdist and tries < 64:
                    l = 3
                    while l < lim and data[cand + l] == data[i + l]:
                        l += 1
                    if l > best_len:
                        best_len, best_pos = l, cand
                        if l == lim:
                            break
                    cand = prev[cand]
                    tries += 1
            if best_len >= TH + 1:
                ring = (best_pos + N - F) & (N - 1)
                out.append(ring & 0xFF)
                out.append(((ring >> 4) & 0xF0) | (best_len - TH - 1))
                for p in range(i, i + best_len):
                    insert(p)
                i += best_len
            else:
                flags |= 1 << bit
                out.append(data[i])
                insert(i)
                i += 1
        out[flagpos] = flags
    return bytes(out)


def byte_sum(d, a, b):
    return sum(d[a:b - 2]) & 0xFFFF


def fix_sums(d):
    for a, b in SUMS:
        s = byte_sum(d, a, b)
        d[b - 2] = s & 0xFF
        d[b - 1] = s >> 8


def check(d):
    ok = True
    if d[0:4] != bytes.fromhex("06d07bad"):
        print("magic: BAD"); ok = False
    if len(d) != 0x100000:
        print("size: BAD", hex(len(d))); ok = False
    for a, b in SUMS:
        s, st = byte_sum(d, a, b), d[b - 2] | d[b - 1] << 8
        print(f"sum {a:#07x}-{b:#07x}: calc {s:#06x} stored {st:#06x} {'OK' if s == st else 'BAD'}")
        ok &= s == st
    for name, base in REGIONS.items():
        size = int.from_bytes(d[base + 0x100:base + 0x104], "big")
        img, end = unlz(d, base + 0x104, size)
        ver = d[base + 0xF8] | d[base + 0xF9] << 8
        print(f"{name}: v{ver >> 8}.{ver & 0xFF:02d} unpacked {size:#x}, stream ends {end:#x} (limit {REGION_END[name] - 4:#x})")
        ok &= end <= REGION_END[name] - 4
    print("ALL OK" if ok else "PROBLEMS")
    return ok


def unpack(path, outdir):
    d = open(path, "rb").read()
    os.makedirs(outdir, exist_ok=True)
    for name, base in REGIONS.items():
        size = int.from_bytes(d[base + 0x100:base + 0x104], "big")
        img, _ = unlz(d, base + 0x104, size)
        open(os.path.join(outdir, name + ".bin"), "wb").write(img)
        print("wrote", name, hex(size))


def pack(stock, mainbin, out, version=None):
    d = bytearray(open(stock, "rb").read())
    img = open(mainbin, "rb").read()
    base, end = REGIONS["main"], REGION_END["main"]
    packed = lz(img)
    back, _ = unlz(packed, 0, len(img))
    assert back == img, "LZSS round trip failed"
    limit = end - 4  # keep build no. + checksum at the end
    if base + 0x104 + len(packed) > limit:
        sys.exit(f"packed main too big: {len(packed):#x} bytes, room {limit - base - 0x104:#x}")
    d[base + 0x104:limit] = b"\xff" * (limit - base - 0x104)
    d[base + 0x100:base + 0x104] = len(img).to_bytes(4, "big")
    d[base + 0x104:base + 0x104 + len(packed)] = packed
    if version:
        maj, mnr = version.split(".")
        d[base + 0xF8] = int(mnr)
        d[base + 0xF9] = int(maj)
    fix_sums(d)
    open(out, "wb").write(d)
    print(f"packed main {len(img):#x} -> {len(packed):#x} bytes ({len(packed) * 100 // len(img)}%), room left "
          f"{limit - base - 0x104 - len(packed):#x}")
    return check(d)


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    if a[0] == "unpack":
        unpack(a[1], a[2])
    elif a[0] == "check":
        sys.exit(0 if check(open(a[1], "rb").read()) else 1)
    elif a[0] == "pack":
        ver = a[a.index("--version") + 1] if "--version" in a else None
        sys.exit(0 if pack(a[1], a[2], a[3], ver) else 1)
    else:
        sys.exit(__doc__)
