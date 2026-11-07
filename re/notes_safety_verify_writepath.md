# Adversarial verify of the write-path claims (notes_safety_verify_writepath)

All updater/loader/booter write-path claims re-read and hold, with these corrections:

1. MAIN key combo (0x31bbf6-0x31bd46): key5 is NOT a gate. Table 0x435da0 {5,5,5}:
   if the three reads are not all nonzero, 0x31bd48 runs a side block (open dev 0x435dd4 via 0x31d50c,
   then 0x36a598) and JUMPs back to 0x31bc54 (0x31bd58 IF CC JUMP 0x31bc54 / 0x31bd70 JUMP.S 0x31bc54).
   If they are all nonzero it goes straight to 0x31bc54. Either way the combo tables are checked:
     0x435d70 {4,2,0} -> P4=1 -> 0x31bda0 ; 0x435d80 {4,3,0} -> P4=2 -> 0x31bda0
     0x435d50 {6,0,0,W=0x34} / {6,1,1,W=0x33} -> 0x32a856 ([0xffb00000]=W), P4=3 -> 0x31bdc4 -> 0x32a8cc
     0x435d90 {0,1,1} -> P4=4 -> 0x31bd84 ; else P4=0 -> 0x31bd74 (normal run loop)
   Whether "nonzero" means pressed or released (active-low) was not checked in the sw driver.
2. Reboot path now traced to the end: 0x32a8cc -> 0x32a902 CALL 0x32a946 (CLI; JUMP 0x300208) ->
   0x300208-0x30021a: R7=[0xffb00000]; JUMP 0x20001000 (loader, booter skipped). Only one caller of
   0x32a856 (0x31bd42) and of 0x32a8cc (0x31bdc8) in main.dis (L1 code there is at the wrong VMA).
3. Source pointer assumes the file is contiguous in the RAM disk. RAM disk buffer is the bss tail
   0x3ca5a0-0x4ea5a0 (0x120000 bytes; bss 0x3bae3c+0x12f764 ends at 0x4ea5a0). The magic scan
   only covers the first 0x64000 bytes. A fragmented file (host wrote extra files / deleted+recopied)
   would flash garbage with no check; loader checksum would then catch main (fallback to updater).
4. Loader: W!=0 (not 0x7x) and updater checksum bad -> hang at 0x2000178c even if main is good.
