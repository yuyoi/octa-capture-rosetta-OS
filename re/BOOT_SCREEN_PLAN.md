# OCTA-CAPTURE v1.51: boot screen facts + Hex girl splash patch plan

TL;DR
- LCD = 128x64 mono, ST7565-family command set (exact part unknown), SPI + A0 + RESET GPIOs. CONFIRMED in code.
- main.bin = `.text` (file 0x0-0x6e56b @ 0x300100+off) followed by a VisualDSP-style init table (1428 records) that builds all
  data. There is no single data delta. Map with the record parser (`re/notes_secmap.md`).
- Two programs draw the stock logo: the flash LOADER (before main, from its own copy) and MAIN's startup screen #0
  (bitmap resource 0xf5 @ 0x433c20 + " Starting..." + version + a 104x8 child widget).
- Plan: replace bitmap 0xf5 in main only. Merge its 5 init records into 1 raw record + 1024 bytes (main.bin +0x238).
  Optionally NOP the 2 text draws and move the bar. Repack with `octa_fw.py pack`. Normal update mode never touches the loader.
- Verified offline here: the rebuilt table parses to EOF, the runtime image is byte-identical to stock when the stock bitmap is
  re-inserted (0 diffs), and the LZSS pack round-trips with checksums OK and 0x78c96 bytes of room left.
- Biggest risk: a main that passes the checksum but hangs before the key-scan task can only be recovered with JTAG or an
  external flash programmer.

Status words: CONFIRMED = read in code/bytes. INFERRED = reasoning, not yet seen on hardware.
Where the input investigations were corrected by their verifiers, the corrected version is used here.

---

## 1. LCD facts

| Item | Value | Evidence | Status |
|---|---|---|---|
| Command set | ST7565/NT7534/S6B0724 family. Init `E3 A2 A0 C8 24 81 2A 2F 40 AC A4 AF A6`, then `81 25` | main 0x366d84-0x366de2 via send-cmd 0x366c94; contrast 0x25 via 0x366e04 from 0x367142; loader 0x2000134c-0x200013ce | CONFIRMED (part number INFERRED) |
| Resolution | 128x64, 8 pages x 128 columns | flush 0x366ebc: 8x setaddr(0,page) + 0x80 bytes (0x366f1a); clear 0x366f38 zeroes 0x100 words; text clip col<21, row<8 (0x366f94/0x366fa0) | CONFIRMED |
| Addressing | `B0\|page`, `10\|col>>4`, `col&F` | 0x366e7c (0x366e94 / 0x366ea2 / 0x366ea8) | CONFIRMED |
| Bus (main) | HW SPI mode 0, 8-bit, MSB first (SPI_CTL 0x5008 PIO, 0x500b/0x510b DMA TX). A0 GPIO at ctx+0x14 (low = cmd), RESET GPIO at ctx+0x18 | updater copy 0x305974, 0x308b8c/0x308bc0; main 0x366c60/0x366c94 | CONFIRMED |
| Bus (loader) | Bit-banged Port G: PG1 = CS, PG2 = SCK, PG4 = SI, PG5 = RESET, PG6 = A0 | 0x2000111c / 0x200011da / 0x20001294 | pin roles INFERRED |
| Driver state | ctx 0x447168; framebuffer 0x447184 (1024 B, ctx+0x1c); contrast 0x447584 (ctx+0x41c) | 0x366ecc, 0x366f42, 0x366e20 | CONFIRMED |
| Device | `"lcd"` registry entry 0x41c620 = {name 0x41c47c, ops 0x419edc, cfg 0x408ddc}. ops = init 0x3670ec, deinit 0x366bfa, open 0x366be8, close 0x366bda, seek 0x366bac, write 0x36719c, read 0x366ba8, ioctl 0x367270 | rebuilt data dump | CONFIRMED |
| ioctl (table 0x43d7e4) | 0 flush, 1 re-init, 2 clear, 3 nop, 4 reverse A6/A7, 5 contrast (<=63), 6 get contrast, 7 puts(col,row,str), 8 putline(row,str), 9 puts_inverse | 0x367288-0x36729e | CONFIRMED |
| write() format | 1024 B row-major, 16 B/row, MSB = left pixel. Each byte is XOR 0xFF, then scattered through the u16 table 0x43cfb8 (file 0x99fe3) into 8x8 tiles. No bounds check: always `seek(0,0)` + exactly 1024 bytes | 0x3671c6-0x3671ea, 0x367248 | CONFIRMED |
| Flush | Transposes each 8x8 tile (L1 0xffa023a8, file 0x9e0a2), 8 pages x 128 B. Net effect: surface (x,y) -> LCD RAM col 127-x, row 63-y (software 180 deg). Natural top-left images show upright, the same way as stock text | 0x366ebc, 0x366efa | CONFIRMED (that this is physically upright: INFERRED) |
| Refresh task | TOPPERS task 0x30e520 (pri 5). Opens lcd, ioctl 2 (clear), ioctl(4,0). Loop: slp_tsk 0x32b540, ioctl 0 (flush), optional reverse flash via [0x418bcc] | 0x30e520-0x30e5b0 | CONFIRMED (waker unknown) |
| Fonts | Driver 6x8 font 0x43ccb8 (file 0x99ce3), columns, bit0 = top. The UI lib has 19 fonts at 0x418bec. The brief's "font at 0x43c964" is an MMR table, not a font | 0x366fd8, 0x30e784 | CONFIRMED |
| UI gfx lib | LCD surface [0x445300] (128x64x1, stride 16), Graphics [0x440948], LCD_Update 0x3481f0 -> 0x300cec -> dev_write("lcd"). DrawString 0x34edd8(g,x,y,str), SetFont 0x34dfdc, FillRect 0x34e5fe/0x34e650, EraseRect 0x34e6aa, SwapColors 0x34dec2 | see gfx angle | CONFIRMED |

Polarity: the driver XORs every byte, and the stock bitmap 0xf5 uses background = 1 bits and wordmark strokes = 0 bits. So
**bit 0 = ink and bit 1 = background** in bitmap and surface data. Whether "ink" looks dark depends on the glass
(positive or negative LCD; unknown). It does not matter for this plan: we copy the stock convention, so the new image
shows with the same contrast as the stock logo.

CPU correction: Port G/H, PORTx_FER and PORTx_MUX (0xffc03210/14/18) plus USB OTG at 0xffc038xx mean the chip is
**not a BF533**. It is most likely a BF52x (BF525/BF527) (INFERRED from the MMR map).

## 2. main.bin address mapping

| main.bin file range | Runtime | Content |
|---|---|---|
| 0x00000-0x6e56b | 0x300100 + off | .text (main.dis is valid here) |
| 0x6e56c | 0x36e66c | pointer -> 0x36e670 (only code reference: 0x30f04c/0x30f050) |
| 0x6e570 | 0x36e670 | table header `00 00 ff ff`, count u32 at file **0x6e574** = 0x594 (1428) |
| 0x6e578-0x9b4b7 | SDRAM 0x400000-0x44de6b (1338 records, no gaps) | data/rodata/bss; delta differs per record (0x391a64..0x3a330c) |
| 0x9b4b8-0x9bccb | L1 data 0xff800000-0xff8034b3 | incl. TOPPERS task table (0xff8003e8..) |
| 0x9bccc-0xa2c8b | L1 code 0xffa00000-0xffa048bd, 0xffa08000-0xffa0a733 | ISRs, SPI/DMA, transpose 0xffa023a8 |
| 0xa2c8c | 0xffb00000-0xffb00673 zero fill | last record, ends exactly at EOF 0xa2c9c |

- Record = `{u32 dest, u32 size, u32 flags, u32 patlen}` + data padded to 4. Kind = (flags & 0x1c0) >> 6:
  0 = copy, 1 = zero (no data), 3 = repeat a patlen-byte pattern. Every record has (flags & 0x3c) == 0 and (flags & 3) == 2.
  Parser loop 0x30f044 (called from reset 0x30012e). On failure it returns nonzero and **hangs at 0x300136**.
- Everything in main.dis at or above 0x36e670 is table data shown as code. L1 code in main.dis sits at the wrong VMA, and
  branches that cross a record boundary after file 0x9d47c are wrong.
- The objdump `.L` annotations are stale; only trust the value on the `.H` line.
- The flat +0x3a2ae8 (main) and the octa_fw.py docstring's "+0x3A0ECC" are only locally right. Use the record parser
  (`re/notes_secmap.md`).

## 3. What the stock boot screen is

Power-on sequence (CONFIRMED in code, timing INFERRED):

1. **Booter** 0x20000000: SDRAM init only, no LCD.
2. **Loader** 0x2000159c:
   - LCD bit-bang init 0x20001294.
   - write() 0x200013e4 of the 1024-B logo at L1 0xff801b28 (loader file records 0x2ee0-0x30b7, identical to main 0xf5).
   - flush 0x20001450.
   - Draws "MAIN V1.51(017B)" at text col 3 on the bottom line (0x2000168c -> 0x200014cc).
   - Checks the main byte-sum, then LZSS-unpacks main to 0x300100 and jumps there.
   - This logo stays up during decompression.
3. **Main**:
   - crt 0x300100 runs meminit 0x30f044, then the kernel starts at 0x32a880.
   - The LCD task 0x30e520 runs ioctl 2 (clear), so a short blank is possible.
4. **HMI screen #0**, via screen table 0x43a980[0] = 0x4358c4 -> handler 0x319a4c, object vtable 0x4025e0:
   - init 0x319808:
     - Reads the version W[0x200200f8] and the build W[0x200efffc].
     - Queues worker 0x319740.
     - Creates child widget [this+0x888] (ctor 0x331fa4) at (10,34), 104x8, range 100. Probably a progress bar (INFERRED).
   - draw **0x319948**:
     - `0x319978 CALL 0x34e0d4` clears the canvas.
     - `0x31999a CALL 0x34e958(canvas, 0, 0, id=0xf5)` draws the 128x64 logo bitmap.
     - `0x3199d6 CALL 0x34eb60` draws "V1.51(017B)" at (60,53).
     - `0x3199e2 CALL 0x34eb60` draws the mode string at (25,41). Index [0x4190f0]: " Starting...", "TESTMODE LINE",
       "TESTMODE FCT", "   UPDATER", "Factory Reset".
   - Worker 0x319740:
     - Loads parameters (0x31cec8, 0x31d074).
     - `0x31978c R0=0x3e8; CALL 0x32b6d8`, most likely dly_tsk(1000), about 1 s.
     - `0x3197b6 CALL 0x35aaf0(0)` posts event 0x21, which leaves the screen.
     - The factory-reset path skips the delay.
   - On-screen time is about 1 s plus parameter load (INFERRED).
5. "Roland"/"OCTA-CAPTURE" strings (0x41b10c/0x41b114) are USB descriptor strings (VID 0x0582, PID 0x0120). The copy at
   0x43e260 goes into SCSI INQUIRY data. " Now Loading" (0x43a138) is the scene-load popup (0x3569ac). None of these is
   the boot screen.

Bitmap resource table 0x434150 (0x106 x 12 B, lookup 0x30e8d8). Entry 0xf5 @ 0x434ccc (file 0x92180) =
`20 3c 43 00 | 80 00 | 40 00 | 10 00 01 00` = ptr 0x433c20, 128x64, 1 bpp, stride 16. The bitmap data at
0x433c20-0x43401f is built by 5 records (CONFIRMED, re-parsed here):

| # | header file | dest | size | kind |
|---|---|---|---|---|
| 960 (0x3c0) | 0x912ec | 0x433c20 | 0x60 | pattern ff |
| 961 | 0x91300 | 0x433c80 | 0xba | copy (data 0x91310) |
| 962 | 0x913cc | 0x433d3a | 0x26 | pattern ff |
| 963 | 0x913e0 | 0x433d60 | 0xc0 | copy (data 0x913f0) |
| 964 | 0x914b0 | 0x433e20 | 0x200 | pattern ff |
| 965 | 0x914c4 | 0x434020 | 0xe9e | copy (next, untouched) |

**The hook to replace: the data of bitmap 0xf5 (runtime 0x433c20, 1024 B).** No code change is needed for the image itself.

## 4. Patch plan: full-screen 1-bit Hex girl (main image only)

### 4.1 Bitmap to generate
- 128x64, 1 bpp, **1024 bytes**, row-major, 16 bytes per row, top row first. **MSB = leftmost pixel.** Natural
  orientation: no mirroring. The mirroring/bit-reversal notes in the investigations apply only to raw controller-RAM
  writes, not to this resource.
- **Ink = 0, background = 1** (same as the stock wordmark).
- From `tools/splash.py 128 64 k out.png` (white = ink):
  ```python
  a = np.asarray(Image.open(png).convert('1'))          # True = ink
  img = np.packbits(~a, axis=1).tobytes()               # ink -> 0, MSB-left, 16 B/row
  assert len(img) == 1024
  ```
- Leave room for overlays you keep (see 4.3). The 104x8 child widget covers x10-113 y34-41 unless it is moved.

### 4.2 Where the bytes go (init-table rebuild)
Replace file bytes **0x912ec-0x914c3** (records 960-964, 0x1d8 bytes) with one copy record:
```
00 3c 43 00 | 00 04 00 00 | 02 00 00 00 | 00 00 00 00   ; dest 0x433c20, size 0x400, flags 2 (copy), patlen 0
<1024 image bytes>
```
- Then append the untouched rest from old 0x914c4 onwards.
- Set the count at **file 0x6e574**: 0x594 -> **0x590** (`90 05 00 00`).
- Keep the pointer at 0x6e56c (0x36e670) unchanged.
- Result: main.bin 0xa2c9c -> **0xa2ed4** (+0x238).
- New record header at file 0x912ec. **Image bytes at new file 0x912fc-0x916fb -> runtime 0x433c20-0x43401f.**
- The image now ends at SDRAM 0x3a2fd4, well below 0x400000. The loader's LZ ring lives in L1. Reset SP = 0xffb01000
  (scratchpad), so nothing lives right after the image during boot (CONFIRMED for the loader and crt; heap placement
  not checked, but the table is dead after meminit).
- `.text` patches below are unaffected (all < 0x6e56c).

Verified in this session:
- Script: `C:\Users\JUNKSM~1\AppData\Local\Temp\claude\0e210060287f58b9\3c78d337-ac01-49fc-a5b8-6f07848b2c7f\scratchpad\build_test.py`.
- Rebuilt with the *stock* bitmap: parses 1424 records to EOF, and the emulated meminit output is **identical to stock
  (0 byte diffs)**.
- With a random 1024-B image: only 0x433c20-0x43401f differs.
- `octa_fw.pack` on both: LZSS round trip OK, all 3 sums OK. Packed 0x57262-0x575c8 vs stock 0x56d04; room left about 0x78900.

### 4.3 Code patches in .text (Blackfin; file offset = addr - 0x300100)

| Patch | Addr / file | Stock bytes | New bytes | Effect | Status |
|---|---|---|---|---|---|
| A | (data, 4.2) | | | new splash bitmap | CONFIRMED mechanism |
| B1 | 0x3199d6 / **0x198d6** | `01 e3 c5 a8` (CALL 0x34eb60) | `00 00 00 00` (2x NOP) | no "V1.51(017B)" at (60,53) | CONFIRMED bytes |
| B2 | 0x3199e2 / **0x198e2** | `01 e3 bf a8` (CALL 0x34eb60) | `00 00 00 00` | no " Starting..."/mode text at (25,41). Test-mode banners disappear too | CONFIRMED bytes |
| C (opt.) | 0x319894 / **0x19794** | `12 61` (R2 = 0x22 = 34) | `c2 61` (R2 = 0x38 = 56) | moves the child widget (bar) to y=56, rows 56-63 | encoding CONFIRMED; that R2 = y: INFERRED |
| D (opt.) | 0x31978c / **0x1968c** | `20 e1 e8 03` (R0 = 1000) | e.g. `20 e1 b8 0b` (3000) | longer splash (sign-extended imm16, max 0x7fff) | INFERRED (dly_tsk units, side effects on boot) |

- B: the call's arguments go unused, and R6/R7 (used after the call) are callee-saved. Safe.
- Do NOT null [this+0x888]: 0x31989c/0x3198a8 dereference it without a check.
- Encoding notes:
  - `R2 = imm7 (X)` = 0x6000 | (imm7 << 3) | reg, stored as a LE halfword.
  - `R0 = imm16 (X)` = halfwords 0xe120, imm16.
  - NOP = 0x0000.
- Do not change `--version`. W[0x200200f8] is also read at 0x319c84 and 0x35b130 (possibly reported over USB to the
  control app).

Recommended first flash: **A + B1 + B2 + C**. D only after the stock timing is known.

### 4.4 Repack
```
python -I build_splash_main.py stock/main.bin hexgirl.bin  out/main_hex.bin   # NOT WRITTEN YET: build_test.py + B/C byte patches
python octa_fw.py pack  stock/UpdOcta_v151.dat  out/main_hex.bin  out/UpdOcta.dat
python octa_fw.py check out/UpdOcta.dat      # must print ALL OK; also unpack and cmp against main_hex.bin
```
- `pack` only replaces the main region (0x20000-0xEFFFB) and fixes all 3 byte-sums.
- Booter, loader and updater stay byte-identical to stock. Diff the .dat against stock to confirm that only
  0x20100-0xEFFFF changed.
- Flash with the **normal** updater mode (R7 = 0x33): it writes 0x20000-0xEFFFF only.

### 4.5 Loader logo (later, optional)
- Same 5-record pattern in the loader table (loader file 0x2ee0-0x30b7 -> L1 0xff801b28).
- The stream would grow 0x3310 -> 0x3548, which still fits before 0x3f00.
- The loader references the table only via 0x20001a28. The sum is at 0x3FFE.
- Needs `[ALL]` mode, which erases the single 16K sector holding the **booter + loader**. Defer until main is proven, and
  only on a stable power supply.
- Without it, the stock OCTA-CAPTURE logo + "MAIN V1.51(017B)" still shows during decompression, before the Hex girl.

## 5. Remaining unknowns -> cheapest experiment

| Unknown | Cheapest resolution |
|---|---|
| Physical orientation and polarity of the glass | Zero-risk: photograph the stock boot. The plan copies the stock convention, so this is only a sanity check |
| Is the (10,34) 104x8 child really a bar, is it drawn, does R2 = y? | Zero-risk: phone video of the stock boot ("Starting..." screen). Patch C confirmed by the first flash |
| Splash duration (dly_tsk tick, parameter load), loader logo time, blank between loader and screen #0 | Phone video of the stock boot at 60 fps |
| Which physical keys = combo indices 5 + {6,1} (normal update) | **Test the updater combo on stock before flashing anything** |
| Does the key-scan task 0x31bba0 run before or after screen #0? | Read main.dis around task activation (desk work); matters only for recovery reasoning |
| Text y origin of 0x34eb60, font used | Not needed if B1/B2 are applied |
| Exact controller part (AC + A4 two-byte quirk) | Irrelevant for drawing |
| Heap/BSS above 0x3a2d9c | Desk: find the malloc arena init (0x325a76 callee). Not needed for boot (table consumed by then) |
| Bitmap 0x15 (152x59, 8 bpp @ 0x430150): a hidden logo? | Desk: render with other strides. Not needed |

## 6. Flashing risks

1. **Unrecoverable hang** (main sum good, but main dies before the key scan). No watchdog (main disables it at 0x3297c4).
   The loader only falls back to the updater on a BAD main sum. A malformed init table hangs at 0x300136 = brick ->
   JTAG or an external flash programmer.
   - Mitigation: the table rebuild is emulated byte-exact offline (done here). Re-run the parse + emulate + diff check on
     the final image: expect diffs only in 0x433c20-0x43401f.
   - Leave crt, the kernel start and task 0x31bba0 untouched (this plan does).
2. **Power loss during the update** (normal mode) corrupts the main region. The bad main sum then makes the loader run the
   updater automatically (updater region untouched), so it is recoverable. The updater ignores flash write errors and
   still says "Update Complete": always `check` the image, and expect "MAIN V1.51(017B)" plus your splash.
3. **[ALL] mode / loader patch** rewrites booter + loader sector 0. A power loss or a bad loader = hard brick.
   Not part of v1.
4. **Settings**: untouched. The param table is unchanged, and normal mode does not write 0xF0000+.
5. **Version field**: unchanged on purpose (other readers at 0x319c84 / 0x35b130).
6. **Optional patches C/D** are INFERRED semantics. A wrong guess draws the bar wrong or changes boot time. A boot
   hang from either is unlikely: C changes one draw coordinate, D one delay argument. Skip D for the first flash.
7. **No flashing done yet.** Before flashing:
   - Diff the final .dat vs stock: only 0x20100-0xEFFFF may change.
   - `octa_fw.py check` = ALL OK.
   - Unpack and compare with the patched main.bin.
   - Run the meminit emulation diff.
   - Confirm the update combo works on stock.
