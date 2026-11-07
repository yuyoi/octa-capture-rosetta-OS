# main.bin section map (OCTA-CAPTURE v1.51) - notes_secmap

## How main.bin is laid out (CONFIRMED)
Reset 0x300100 -> CALL 0x30f044 (VisualDSP++ meminit, `_mi_initialize`-style) before EVT setup.
0x30f04c/0x30f050: P1 = 0x36e66c; R0 = [P1] -> table pointer = 0x36e670 (file 0x6e570).
Table header: u32 0xffff0000 (W[+2]==0xffff, B[+0]==0 checked at 0x30f062-0x30f070), u32 count = 0x594 (1428).
Each record: u32 dest, u32 size, u32 flags, u32 patlen, then data.
  kind = (flags & 0x1c0) >> 6   (0x30f0ac-0x30f0b2)
  kind 0 (flags 0x02): copy `size` bytes that follow, then align 4  (0x30f298-0x30f2f6)
  kind 1 (flags 0x42): zero-fill `size` bytes, no data               (0x30f1dc-0x30f260)
  kind 3 (flags 0xc2): fill `size` bytes repeating `patlen`-byte pattern that follows, align 4 (0x30f0c2-0x30f11c)
  dest in 0xffa00000..0xffa14000 (L1 instr) goes through DMA helper 0x30ee24 instead of core writes.
Parsing all 1428 records ends exactly at EOF 0xa2c9c.

| file range        | runtime range             | what |
|-------------------|---------------------------|------|
| 0x00000-0x6e56b   | 0x300100-0x36e66b (VMA = 0x300100+off) | .text (SDRAM code) |
| 0x6e56c-0x6e56f   | 0x36e66c                  | pointer to meminit table |
| 0x6e570-0x6e577   | 0x36e670                  | meminit header |
| 0x6e578-0x9b4b7   | 0x400000-0x44de6b (1338 recs) | SDRAM .data/.rodata/.bss, contiguous, piecewise delta (0x391a70..0x3a330c) |
| 0x9b4b8-0x9bccb   | 0xff800000-0xff8034b3 (44 recs) | L1 data bank A |
| 0x9bccc-0xa2c8b   | 0xffa00000-0xffa0a733 (45 recs) | L1 INSTRUCTION code (ISRs, SPI/DMA, hot loops) |
| 0xa2c8c-0xa2c9b   | 0xffb00000-0xffb00673     | scratchpad zero-fill |
SDRAM data: 0x27180 copied + 0x243fd zero + 0x28ef pattern bytes.
NOTE: main.dis lines 0x36e670..0x3a2c9c are this table disassembled as code. L1 code
(main.dis 0x39bddc..0x3a2d8b) is shown at the wrong VMA and pc-relative CALL targets are
wrong after the first pattern-fill record (0xffa017a0). E.g. the "SPI driver 0x39d7e0" is really L1 0xffa019e6.
Vectors check: 0xffa012d8 -> main.dis 0x39d0b4 "[--SP]=P0; P0=6; JUMP" (IVG6), 0xffa014e4 -> 0x39d2c0 "P0=0xd" (IVG13).

## addr_to_off (reuse)
```python
import struct, bisect
MAIN = 'stock/main.bin'  # your own copy: python octa_fw.py unpack UpdOcta.dat stock
BASE = 0x300100
_d = open(MAIN, 'rb').read()

def _parse(d=_d):
    tbl = struct.unpack_from('<I', d, 0x36e66c - BASE)[0]
    p = tbl - BASE
    magic, cnt = struct.unpack_from('<II', d, p); p += 8
    assert magic == 0xffff0000
    segs = []   # (addr, size, kind, file_off_or_None, pattern_len)
    for _ in range(cnt):
        dest, size, flags, dsz = struct.unpack_from('<IIII', d, p); p += 16
        kind = (flags & 0x1c0) >> 6          # 0=copy 1=zero 3=pattern fill
        if kind == 0:
            segs.append((dest, size, 0, p, 0)); p = (p + size + 3) & ~3
        elif kind == 1:
            segs.append((dest, size, 1, None, 0))
        else:
            segs.append((dest, size, 3, p, dsz)); p = (p + dsz + 3) & ~3
    assert p == len(d)
    return tbl - BASE, segs

TABLE_OFF, SEGS = _parse()
CODE_END_OFF = TABLE_OFF - 4                 # 0x6e56c: .text = file 0..0x6e56c @ 0x300100
_S = sorted(SEGS); _A = [s[0] for s in _S]

def addr_to_off(addr):
    """Runtime address -> file offset in main.bin (None if bss/zero/pattern or unmapped)."""
    if BASE <= addr < BASE + CODE_END_OFF + 4:
        return addr - BASE
    i = bisect.bisect_right(_A, addr) - 1
    if i >= 0:
        a, sz, kind, fo, pl = _S[i]
        if a <= addr < a + sz:
            if kind == 0: return fo + (addr - a)
            if kind == 3: return fo + (addr - a) % pl   # repeated pattern byte
            return None                                    # zero-filled (bss)
    return None

def off_to_addr(off):
    if 0 <= off < CODE_END_OFF + 4: return BASE + off
    for a, sz, kind, fo, pl in SEGS:
        if kind == 0 and fo <= off < fo + sz: return a + (off - fo)
    return None
```

## Target strings
| string | file | runtime | referenced from |
|---|---|---|---|
| "Roland" | 0x78d28 | 0x41b10c | no code imm; data ptrs @0x41b6bc and @0x41b740 (USB descriptor sets 0x41b6a4 / 0x41b728, VID 0x0582 PID 0x0120), sets listed at 0x41b0fc, read by 0x369f9c/0x369fb6/0x369fe8 |
| "OCTA-CAPTURE" | 0x78d30 | 0x41b114 | data ptrs @0x41b6c0 and @0x41b744 (same USB sets) |
| " Now Loading" | 0x9717c | 0x43a138 | 0x3569ac: R2 = P5(0x43a09c set at 0x356794/0x356798) + 0x9c, case 12 of switch tbl 0x43a464 -> CALL 0x359eec with " SCENE No. %d", 2000 |
| "LCD CONTRAST" | 0x7b047 | 0x41d3a0 | 0x30d240: R1 += 0xc on base 0x41d394 (0x30d226/0x30d22a), case 0 of switch tbl 0x41d508 -> CALL 0x324010 |
| "LCD SPI Cannot Get" | 0x9a7e3 | 0x43d7b8 | 0x36718c/0x367190 R0 = 0x43d7b8; CALL 0x32aad8 |
Also: 2nd Roland/OCTA-CAPTURE pair file 0x9b2e0/0x9b2e8 -> 0x43e260/0x43e268, used at 0x36d44c (8-char vendor/16-char product, "1.00" -> SCSI INQUIRY-like block at 0x44d4cc).
6x8 font: base 0x43ccb8 (file 0x99ce3), char*6 bytes, column format; used at 0x366fd2-0x366ff0 and 0x36708e.
