# What the v1.51 updater erases and writes (notes_safety_updater_writes)

Addresses: updater code = re/upd.dis (VMA 0x300100+off). Updater DATA is NOT off+0x3a0ecc:
it is placed by the meminit table at updater.bin file 0x16a60 (same format as main, 172 records,
ends at EOF 0x1e070). E.g. 0x3b8820 = file 0x178b8 "Ocata-Capture Updater"; "Put file, and Unplug" = 0x3b88bc (file 0x17954).

## Copy table 0x3b8810 (file 0x178a8)  CONFIRMED
| idx | start | len | selected when |
|---|---|---|---|
| 0 (+0) | 0x00000 | 0xF0000 | mode flag [0x3bae50] != 0  -> "[ALL]" |
| 1 (+8) | 0x20000 | 0xD0000 | mode flag == 0 (normal) |
0x300f4c-0x300f94: R1=[P4]; CC=R1==0; R1=CC<<3; P1=0x3b8810+R1; [SP+0x24]=[P1] (start), [SP+0x28]=[P1+4] (len).
0x300fb6 seek(nor_flash, start, SEEK_SET); source = RAM-disk addr of magic 0xAD7BD006 (file offset 0) + start.
Loop 0x301028-0x301092 writes 0x200-byte blocks via 0x3086e8 until len <= written. Flash dst = 0x20000000+start.
0xffa02de8 / 0xffa0321c are NOT erase: they are L1 libgcc-style unsigned div / mod (updater.bin file 0x1d366 / 0x1d79a,
L1 record 0xffa02122<-file 0x1c6a0), used only for the 17-step progress bar (R7 = len/17 via magic 0xF0F0F0F1 at 0x3b8808).

## nor_flash driver  CONFIRMED
dev table 0x3b86f8 -> "nor_flash" ops 0x3b6c1c: open 0x309744, close 0x30986c, seek 0x309714, write 0x309888, read 0x309a20.
write = sector read-modify-write cache (state 0x3c4dec). Flush 0x3097ac -> 0x30a574: erase sector (AA/55/80/AA/55/30, 0x30a420),
program words skipping 0xFFFF (AA/55/A0, 0x30a4b8), verify (0x30a1b6), up to 4 tries. Failure -> "Flash Write Error" via
0x304074 (sets flag only; no reader found) and the updater still shows "Update Complete".
Sector map 0x3ba208: 1x16K @0, 2x8K @0x4000, 1x32K @0x8000, 15x64K @0x10000 (bottom-boot, 1 MB).
Only sectors touched by the write range get erased.

## Result
- normal: erases+programs 0x20000-0xEFFFF (13x64K). Booter, loader (0x0-0x3FFF), updater (0x4000-0x1FFFF), 0xF0000+ untouched.
- [ALL]: erases+programs 0x00000-0xEFFFF incl. the single 16K sector holding booter+loader. 0xF0000+ untouched.
- HwTest: "UpdOctaHwTest.dat" is only a fallback filename (0x301140-0x301164 -> same path 0x300f18). Same ranges as above.
- Updater checks only: size == 0x100000 (0x300f2c) and magic word found in RAM disk (0x300fbe). No checksum/version check before writing.

## Mode selection
loader 0x2000104a saves entry R7 -> [0xff802360]; 0x20001116 passes it as R7 to updater; updater 0x300140 stores it at
[0xffb00000]; 0x300898: flag = ([0xffb00000] & 0xF) == 4.
Booter always enters loader with R7=0 (0x2000010a). Loader with W=0: main sum OK -> main; main sum bad and updater sum OK
-> updater with W=0x33 (normal) (0x20001834-0x20001838). W!=0 (not 0x7x) -> updater if updater sum OK.
W is set != 0 only by MAIN at power-on key scan 0x31bbf6-0x31bd46 ("sw" device, 9 key bytes; table 0x435d50):
key5 must be held, then keys {6,0} -> W=0x34 ([ALL]) ; keys {6,1} -> W=0x33 (normal); then reboot 0x31bdc4->0x32a8cc.
=> The updater combo lives in MAIN. A modified main that passes the loader checksum but dies before 0x31bbf6 cannot
reach the updater (only via a bad checksum). Keep the key scan intact.
