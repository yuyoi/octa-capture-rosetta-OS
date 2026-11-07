# main.bin flash writes / settings storage (OCTA-CAPTURE v1.51) - notes_safety_main_flash

## TL;DR
- main has ONE flash writer: device "extmem" (= updater's "nor_flash"). Every caller seeks into the LAST 64 KB sector 0xF0000-0xFFFFF.
- Settings: flash 0xF0040, 0xD1C bytes (magic 0x55AAFF00, then version word 0x1F), plus an 8-byte trailer at 0xF0D5C.
  ID record "ISRL": flash 0xF0000, 12 bytes (written only in test mode).
- No firmware-update or flash-write path into 0x0-0xEFFFF in main. "SYSTEM UPDATE" is only a USB descriptor string set (PID 0x0125).
  main can soft-reset into the loader (JUMP 0x20001000) with a boot param (0x33/0x34) when a power-on key combo is held.

## Driver (CONFIRMED)
Device registry entry 0x41c6b0: name "extmem" (0x41c4c4), ops 0x419f5c, param 0x408da0 (zeros).
ops: +0 init 0x367bca, +4 deinit 0x367c18, +8 open 0x367b18, +0xc close 0x367c48 (flush),
     +0x10 lseek 0x367ae8 (SET/CUR/END, clamps to 0xFFFFF), +0x14 write 0x367c68, +0x18 read 0x367e18.
State at 0x4487a0: +4 position, +8 cached sector index, +0xc dirty, +0x10 sector buffer, +0x14 buffer size.
Address = 0x20000000 | pos (0x367c80 BITSET bit 29; read 0x367e2c).
Write = read-modify-write sector cache: 0x367d84 memcpy(buf, sector_start, sector_size), then the user bytes are copied in;
flush 0x367b80 -> 0x367b40 -> 0x368e44 -> 0x368dec: erase (0x368c98: AA/55/80/AA/55/30), program words
(0x368d30: AA/55/A0, skips 0xFFFF words), verify (0x368a2e); up to 4 tries; on fail print "Flash Write Error"
(0x367bbc -> 0x32aad8, not a hang).
Globals: 0x419fe4 = 0x20000000, 0x419fe8 = sector table 0x43daa0 = {1x16K, 2x8K, 1x32K, 15x64K} (whole 1 MB chip).
Sector map: idx0 0x00000-0x03FFF (booter+loader!), idx1-2 0x4000/0x6000 (8K), idx3 0x8000 (32K),
idx4..18 0x10000.. 64K each; idx18 = 0xF0000-0xFFFFF (settings).
Low-level erase/program has a single caller chain (0x367b40 <- 0x367b80 <- extmem write/close).

## Callers (CONFIRMED, open "extmem" via 0x366aa4; lseek 0x3667b0; read 0x366730; write 0x366770; close 0x3667ec)
| fn | mode | seek | len | caller |
|---|---|---|---|---|
| 0x31d074 load  | read  | 0xF0040 | [0x419110]=0xD1C -> buf [0x419114] | boot 0x319766 |
| 0x31d160 save  | write | 0xF0040 | 0xD1C from [0x419114] | power-off 0x3192b8 ("Saving Parameters"), 0x31e3a4 |
| 0x31d1d4 check | read  | 0xF0040+len | 8 (trailer, checks magic+0x1F) | 0x30f4b6 |
| 0x31d27c defaults | write | 0xF0040 (buf [0x419118]) then 8x 0xFF at 0xF0040+len | | boot 0x31977e (when load fails), 0x35e794 |
| 0x31d334 load2 | read  | 0xF0040 | 0xD1C | 0x31e3c2 |
| 0x31d404 save2 | write | 0xF0040 | 0xD1C | 0x31e3aa |
| 0x31d47c ID write | write | 0xF0000 | 12 | test mode 0x35b1b0 only |
| 0x31d50c / 0x31d598 / 0x31d670 ID read | read | 0xF0000 | 12 | 0x31bd52/5c/64, test mode |
Length 0xD1C computed at 0x31cec8 from 52 entries at 0x435e0c (size = Ta[f&3]*Tb[(f>>8)&7]*Tc[(f>>16)&7]),
error "CPrmMemory Memory Over" if > 0x10000. Magic 0x55AAFF00 / 0x1F are the defaults of entries 0 and 1.

## Safety consequences
- Stock main touches only sector 18. Power loss during a save can corrupt only 0xF0000-0xFFFFF
  (settings + ID record); main then writes defaults at the next boot. Not a brick.
- A patched main that changes the param table (length or the 0x1F version) will fail the load and rewrite defaults
  at the first boot. That is harmless, but the user settings are lost.
- The driver itself can address the whole chip. A patch that breaks the seek offset, or calls the low-level routines
  with a low sector index, could erase sector 0 (booter+loader) = hard brick. Do not touch 0x31d0xx-0x31d6xx,
  0x367ae8-0x367e18 or 0x368a00-0x368e84 in patches.
- The update file holds 0xDEAD in 0xF0000-0xFFFFF. Whether the updater writes it (wiping settings + ID) is NOT checked here.
