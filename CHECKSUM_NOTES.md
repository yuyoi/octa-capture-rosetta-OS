# Roland OCTA-CAPTURE firmware: checksums and packing

These are notes from reverse-engineering `UpdOcta.dat` (system v1.51, 1 MB) for the Roland OCTA-CAPTURE (UA-1010) audio interface. Everything here was checked against the real file and the real code. The method at the end carries over to other gear.

## TL;DR
- **The checksum is a plain 16-bit byte sum.** It covers each region except its last 2 bytes, and it's stored little-endian in those last 2 bytes. There's no CRC.
- **The update program checks almost nothing.** It looks at a 4-byte magic number and the file size (exactly 1 MB). The real check is done by the boot code at every power-on.
- **The code is packed with LZSS, with one twist.** The history buffer starts full of zeros, not spaces. Get that wrong and roughly 600 bytes per program come out corrupted, yet the sizes still match, so it's easy to miss.

## File layout (offset in the file = address in the unit's flash, starting at 0x20000000)
| Range | What | Format |
|---|---|---|
| `0x00000-0x00FFF` | booter | raw Blackfin code (ADSP-BF52x; the strings say BF533), runs straight from flash |
| `0x01000-0x03FFF` | loader | raw code |
| `0x04000-0x1FFFF` | updater (shows up as a USB drive) | 0x100-byte header + LZSS |
| `0x20000-0xEFFFF` | main program | 0x100-byte header + LZSS |
| `0xF0000-0xFFFFF` | filler | `DE AD DE AD ...` |

Header of a packed region:
- `+0x00`: text, e.g. `EDIROL VS100 application program / Copyright Roland Corp. / Build date <...>`. It's built on the VS-100 code.
- `+0xF8`: version, 16-bit little-endian. The high byte is the major version and the low byte the minor: `33 01` = v1.51.
- `+0x100`: size of the unpacked program, 32-bit **big-endian**.
- `+0x104`: the LZSS data. Unused space after it is `FF`.

The last 4 bytes of the loader, updater and main regions are `7B 01 <sum lo> <sum hi>`. `0x017B` is a build number that appears in every region.

## The checksum
```
sum   = (sum of every byte in [start, end-2)) & 0xFFFF
flash[end-2] = sum & 0xFF      # little-endian
flash[end-1] = sum >> 8
```
| Region | Summed range | Stored at | Stock value |
|---|---|---|---|
| loader | `0x1000-0x3FFD` | `0x3FFE` | `0xBF12` |
| updater | `0x4000-0x1FFFD` | `0x1FFFE` | `0x7107` |
| main | `0x20000-0xEFFFD` | `0xEFFFE` | `0xFC91` |

The sum includes the `7B 01` build word just before it. The booter (`0x0-0xFFF`) has no sum of its own; the loader's range starts at `0x1000`.

Where it's checked: the loader code at flash `0x20001732-0x2000183C`. It's a tight byte-add loop (`R0 = R0 + R1 || R1 = B[P1++]`). It compares the low byte with `[end-2]`, then the high byte with `[end-1]`. If the updater's sum is wrong, it hangs on a jump-to-itself. Main takes a different branch.

## The update program's own check (weak)
The update program shows up on your computer as a USB drive. It reads `UpdOcta.dat` from it and checks only two things:
1. The file is exactly 1 MB.
2. The magic number `0xAD7BD006` (bytes `06 D0 7B AD`) is present. It's the first word of the file.

It doesn't check the sums at all. Then it writes the file in 512-byte blocks. So a file with a bad sum gets flashed happily and only fails at the next boot. **Always fix the sums yourself.**

## The LZSS variant
This is classic Okumura LZSS (N=4096, F=18, THRESHOLD=2):
- One flag byte before every 8 items, read **least-significant bit first**. A `1` bit means one literal byte.
- A `0` bit means a 2-byte back-reference. Position = `b1 | (b2 & 0xF0) << 4` (a position in the 4 KB history buffer, not a distance). Length = `(b2 & 0x0F) + 3`.
- The write position in the buffer starts at `0xFEE`.
- **The buffer starts filled with `0x00`.** Most tools fill it with spaces (`0x20`).

```python
def unlz(src, pos, size):
    win = bytearray(4096); r = 0xFEE; out = bytearray(); flags = 0
    while len(out) < size:
        flags >>= 1
        if not flags & 0x100:
            flags = src[pos] | 0xFF00; pos += 1
        if flags & 1:
            c = src[pos]; pos += 1
            out.append(c); win[r] = c; r = (r + 1) & 0xFFF
        else:
            i = src[pos] | ((src[pos + 1] & 0xF0) << 4)
            n = (src[pos + 1] & 0x0F) + 3; pos += 2
            for k in range(n):
                c = win[(i + k) & 0xFFF]
                out.append(c); win[r] = c; r = (r + 1) & 0xFFF
    return bytes(out[:size]), pos
```
How we caught the space-vs-zero bug:
- With spaces, the unpacked size matched and the data ended exactly where it should.
- But every function in the disassembly started with `20 E8` instead of `00 E8` (Blackfin `LINK`).
- Short repeated runs of zeros in the code had been copied from the early "spaces", and that spread through the whole image.

## Repack recipe
1. Pack the new program with the same LZSS. Any greedy LZSS encoder that never refers to buffer bytes not yet written is fine. Decompress your output again to check it.
2. Write the size (big-endian) at `+0x100` and the packed data at `+0x104`. Fill the rest with `FF` up to `end-4`.
3. Keep the `7B 01` at `end-4` and recompute the 16-bit sum into `end-2`/`end-1`.
4. Keep the magic number and the 1 MB size.

Our repacked main isn't byte-identical to Roland's, because the encoder is different. It unpacks to the identical program and passes every check.

## How we found it (works on other gear)
1. **Brute force first.** We tried sum8/16/32, XOR, CRC16 variants, CRC32 and Adler over every plausible range. **None matched**, because the obvious "last 4 bytes" field (`7B01 91FC`) was really a build number plus the sum. The sum only lines up over the right range: start of region to `end-2`.
2. **Read the code that checks it instead.**
   - Build `bfin-elf` binutils from GNU source (`--target=bfin-elf`). Debian's `binutils-multiarch` doesn't include Blackfin.
   - Disassemble the raw boot area as plain binary at `0x20000000`.
   - Search for immediates near the region ends (`0x2001FFFE`, `0x200EFFFE`). The sum loop sits right there.
3. **Find the update-file check** by searching the updater for the magic number's immediate (`R5.H = 0xAD7B`).
4. **Confirm by recomputing** the sum on all three regions of the stock file. All three matched.

Tool: `octa_fw.py` (`unpack` / `pack` / `check`) in this folder.
