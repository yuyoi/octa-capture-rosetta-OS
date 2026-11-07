# OCTA-CAPTURE v1.51: is it safe to flash a modified main region?

TL;DR
- **Verdict: CONDITIONALLY SAFE.** A normal-mode update writes only flash 0x20000-0xEFFFF. The booter, the loader, the updater and the settings sector are never touched.
- **The safety net:** recovery through the updater needs one of two things:
  - (a) the main checksum is bad, in which case the loader starts the updater on its own; or
  - (b) the patched main is still alive when it reaches its key scan at 0x31bbf6, so the button combo can be read.
- **The one real brick case:** a main whose checksum is correct but which hangs or crashes before that key scan. Getting out of that needs JTAG or a flash programmer.
- **Rule 1:** never patch anything that runs before 0x31bbf6.
- **Rule 2:** never enter the updater with the [ALL] combo.

Status tags: CONFIRMED means I read it in the code. INFERRED means it is reasoned from code I read, but not traced all the way.
Sources: notes_safety_updater_writes.md, notes_safety_bootflow.md, notes_safety_main_flash.md and notes_safety_verify_writepath.md. Every claim below survived an adversarial re-check, with corrections applied.

## Corrections to the earlier "verified facts"

**CPU.** The chip is a BF52x with USB (BF524/525/526/527), not a BF533.
- The code uses registers that do not exist on a BF533: PORTG/PORTH at 0xffc015xx/0xffc017xx, PORTx_FER at 0xffc03204/08, MDMA at 0xffc00f00+, and USB_POWER at 0xffc03804 (main 0x3297d2).
- The boot header at 0x20000000 is a 16-byte block in the BF52x format with a valid XOR checksum (FIRST|FINAL|IGNORE, target 0x20000020).
- Status: INFERRED, but strongly supported.

**Updater data addresses.** The rule "updater data address = file offset + 0x3a0ecc" is wrong.
- Updater data is placed by a meminit table. The table pointer is at [0x316b5c] and the table starts at updater.bin file offset 0x16a60. It has 172 records and ends exactly at EOF, 0x1e070.
- 0x3b8820 is the string "Ocata-Capture Updater". "Put file, and Unplug" is at 0x3b88bc. The copy table 0x3b8810 is at file offset 0x178a8.
- Status: CONFIRMED.

**Main's flash device.** In main the NOR device is named "extmem". The name "nor_flash" exists only in the updater. Status: CONFIRMED.

---

## 1. What a normal update rewrites

**Normal mode (boot word W=0x33) erases and programs flash 0x20000-0xEFFFF only.** That is 13 sectors of 64 KB. Status: CONFIRMED.

- **Which range.** The copy table at 0x3b8810 holds `{0x00000,0xF0000}` (entry 0) and `{0x20000,0xD0000}` (entry 1). The mode flag picks the entry:
  - 0x300f4c `R1=[P4]` with P4=0x3bae50
  - 0x300f54 `CC=R1==0`
  - 0x300f56 `R1=CC`
  - 0x300f58 `R1<<=3`
  - 0x300f82 `P1=P0+P1`
  - 0x300f84 `R1=[P1+4]` (length), 0x300f8e `R1=[P1]` (start)

  So flag 0 (normal mode) selects entry 1.
- **Where it writes.** The updater calls `seek(handle, start, SEEK_SET)` (0x308728, which goes to 0x309714 and clamps the offset to 0xFFFFF). It then writes 0x200-byte blocks through 0x3086e8 (which goes to 0x309888) until `CC = len <= R4` (0x301056-0x301070). Both lengths are multiples of 0x200, so exactly `len` bytes are written.
- **Where the data comes from.** The updater scans RAM from 0x3ca5a0 for the first word 0xAD7BD006 (0x300f6a-0x300f7e, loop 0x300fbe-0x300fcc, at most 0x19000 words). It then adds `start` (0x300fe4, 0x30101e). So data at file offset X is written to flash offset X.
- **Erase granularity.** "nor_flash" is a cache that works on whole sectors. On a sector change and on close it calls flush 0x3097ac, which goes 0x30976c -> 0x30a5cc -> 0x30a574. Only sectors that the write range touched are erased.
  - 0x20000 is the start of a 64 KB sector, and 0xEFFFF is the last byte of one.
  - So the 0x10000-0x1FFFF sector, which holds the tail of the updater, is never erased.
- **Sector map.** It is a bottom-boot layout: one 16 KB sector at 0x0, two 8 KB sectors at 0x4000 and 0x6000, one 32 KB sector at 0x8000, then fifteen 64 KB sectors from 0x10000. The map is at 0x3ba208, pointed to by [0x3b6c88]. Status: CONFIRMED.
  - **The booter and the loader share sector 0 (0x0-0x3FFF).**
- **What the updater checks before writing.** Only two things (CONFIRMED, 0x300f18-0x301068):
  - The file size is exactly 0x100000: `ftell` at 0x300f28, then `BITTGL bit20` / `CC=R0==0`.
  - The magic word is present.

  It does not check the checksum or the version. The loader checks the checksums at the next boot.
- **Write errors are hidden.** Erase, program and verify use AMD command sequences: erase at 0x30a420, program at 0x30a4b8 (which skips 0xFFFF words), verify at 0x30a1b6, with 4 tries (0x30a58a). Status: INFERRED.
  - On failure the updater only sprintfs "Flash Write Error" (0x3097e8 -> 0x304074). That routine calls the hook at [0x3b6a08] only if the hook is set, and no code that writes that hook was found.
  - The UI still shows message 4, "Update Complete" (0x30109a-0x30109c).
  - Mitigation: a bad write to main breaks the main checksum, so the loader falls back to the updater at the next boot (section 2).
- **[ALL] mode (W=0x34) erases and programs 0x00000-0xEFFFF.** That covers the booter, the loader, the updater and main. Status: CONFIRMED.
  - A power cut while sector 0 is being written is a hard brick.
- **0xF0000-0xFFFFF is never written by the updater, in either mode.** That is the settings sector. Status: CONFIRMED.
- **"UpdOctaHwTest.dat" is only a fallback filename.** It is tried only if "A:\UpdOcta.dat" fails to open (0x300f14 -> 0x301140 -> 0x301164 `IF !CC JUMP 0x300f18`). It then goes through the same size, magic and table path. Status: CONFIRMED.
- **Caveat: the file must be contiguous in the RAM disk.** The source pointer is simply magic position + offset.
  - The RAM disk is the bss tail 0x3ca5a0-0x4ea5a0 (0x120000 bytes), and the magic scan covers only its first 0x64000 bytes.
  - If FAT fragments the file, garbage gets flashed with no check. The loader checksum would then catch a corrupted main.
  - Status: INFERRED.

## 2. Boot decision, and what happens when main is bad or crashes

**Booter (0x20000000-0x20000FFF): reads no buttons.** Status: CONFIRMED.
- It runs meminit 0x20000324 and sets up SDRAM in 0x20000110.
- Then: 0x20000100-0x2000010e `CLI; P0=0x20001000; R7=0x0 (Z); JUMP (P0)`. **Every cold boot enters the loader with R7=0.**
- No code in the booter or the loader reads PORTGIO/PORTHIO data, SPI or TWI. The direction, input-enable and function-enable registers are only read-modify-written.

**Loader (0x20001000-0x20003FFF).** Status: CONFIRMED.
- It runs meminit 0x20001a1c, then stores the incoming R7 with 0x2000104a `[0xff802360]=R7`.
- It initialises the LCD (0x20001294), draws the boot bitmap and "MAIN V%d.%02d(%04X)" using W[0x200200F8] and W[0x200EFFFC] (0x200015fc-0x2000160c). **This happens before it decides what to run.**
- Then it dispatches (0x20001690-0x20001838), where W is the incoming R7:

  | W | Checks | Result |
  |---|---|---|
  | 0 (every cold boot) | main sum over [0x20000,0xEFFFE) vs B[0xEFFFE]/B[0xEFFFF] (0x20001790-0x200017d8) | **good**: run main (0x200017de `[0xff801014]=1`) |
  | 0, main sum bad | updater sum over [0x4000,0x1FFFE) vs 0x1FFFE/F (0x200017e4-0x20001838) | good: run the updater with W=0x33 (normal mode, never [ALL]). Bad: hang at **0x2000183c** `JUMP.S self` |
  | nonzero, (W&0xF0)!=0x70 | updater sum only (0x20001736-0x2000178a) | good: run the updater. Bad: hang at **0x2000178c**, even if main is good |
  | (W&0xF0)==0x70 | none | "SD BOOT": wait, then jump to 0x300100 without unpacking anything. Nothing in main or the updater sets this value (INFERRED: a JTAG/dev path) |

- **How main is unpacked.** The loader reads the BE32 size at region+0x100. If the size is <=0 (signed) it hangs at 0x20001702. Otherwise it runs the L1 LZSS routine at 0xffa0811c (flash copy at 0x20003238) into 0x300100. Status: CONFIRMED.
  - The routine does no CRC and no check of the output.
  - It returns only when it meets a literal token after the output is already full (0x200032ea).
  - **The 16-bit byte-sum is the only integrity check.**
- **Loader exit.** 0x20001104-0x20001118 `CLI; P0=0x300100; R7=[0xff802360]; JUMP (P0)`. Status: CONFIRMED.

**The update button combo is read by MAIN, not by the booter or the loader.** Status: CONFIRMED.
- The scan runs in TOPPERS task 0x31bba0 (its task record is at L1 0xff800550, priority-looking value 5, stack 0x1800).
- Before the scan the task makes these init calls:
  - 0x300d84
  - 0x30e5b4
  - 0x300be0
  - 0x325a76
  - 0x34f31c (conditional)
  - 0x329b0c
- The scan itself:
  - 0x31bbf6-0x31bc00: open "sw" (0x435d4c) with CALL 0x366aa4.
  - 9 key bytes are read via 0x3666d8, starting at 0x31bc0c.
  - A row matches when all 3 of its key bytes are nonzero.
- Rows, in the order they are checked:
  1. `{5,5,5}` (0x435da0). This is NOT a gate. If it fails, a side block runs at 0x31bd48 (ID read from 0xF0000) and execution returns to 0x31bc54.
  2. `{4,2,0}` -> mode 1, then `{4,3,0}` -> mode 2 (0x435d70 / 0x435d80, -> 0x31bda0).
  3. **`{6,0,0}` -> W=0x34 ([ALL])**, then **`{6,1,1}` -> W=0x33 (normal)** (0x435d50).
     - 0x31bd34-0x31bd42 load `R0=[entry+0xc]` and `CALL 0x32a856` (`[0xffb00000]=R0`), then set mode 3.
     - Mode 3 runs 0x31bdc4 -> 0x32a8cc -> 0x32a902 `CALL 0x32a946` (`CLI; JUMP 0x300208`).
     - 0x300208-0x30021a then does `R7=[0xffb00000]; JUMP 0x20001000`. This is a soft re-entry into the loader; the booter is skipped.
  4. `{0,1,1}` -> mode 4. This writes default settings: a factory reset (0x319770).
  5. No match -> mode 0, a normal boot.
- That a nonzero byte means "pressed" is INFERRED; the polarity of the sw driver was not checked.
- **Which physical buttons are key indices 0, 1 and 6 is UNKNOWN.**

**The updater picks [ALL] mode from W.** The test is `([0xffb00000]&0xF)==4` (upd 0x300898-0x3008ae, stored to [0x3bae50] at 0x300d4c). So W=0x34 means [ALL] and W=0x33 means normal. Status: CONFIRMED.

**No watchdog.** Main writes WDOG_CTL=0x0AD0 (disabled) at 0x3297b8-0x3297c4. Neither the booter nor the loader touches 0xffc002xx. Status: CONFIRMED. (That the boot ROM leaves the watchdog off is INFERRED from the reset default.)

**Outcomes for a modified main:**
- **Main checksum wrong** (a builder bug, a bad write, a fragmented RAM-disk file): the loader runs the updater automatically in normal mode. **Recoverable without buttons.** Status: CONFIRMED.
- **Checksum good, but the LZSS stream is bad or the code crashes before 0x31bbf6:** the loader has already jumped to 0x300100. No watchdog fires and no combo can be read. Every power cycle repeats the same failure. **Brick, needing JTAG or a flash programmer.** Status: INFERRED from confirmed parts.
- **Checksum good, and the crash comes after the combo scan has run:** recoverable with the normal combo `{6,1}` held at power-on. Status: INFERRED.
- **Caveat on "after the scan":** TOPPERS runs other tasks and interrupt handlers alongside the scan task. A patched routine in a higher-priority task, or one started earlier, can kill the system before the scan task gets the CPU, even if that routine is not called from the 0x31bba0 path. Status: INFERRED.

## 3. Recovery procedure if the patched main fails

**Step 0: do this before flashing anything patched.** This is the "one test we can afford".
- On STOCK firmware, find out which physical buttons are key 6 and key 1. Use the normal combo `{6,1,1}`, and find it by elimination or from Roland's documented update procedure.
- Confirm the screen shows the updater header **without** "[ALL]" ("        ver:..."). If it shows "[ALL]   ver:", power off immediately, before copying any file.
- Keep a known-good copy of stock UpdOcta.dat (1,048,576 bytes) on the PC.

**Case A: the unit shows the updater on its own after the flash.** This is the bad-checksum fallback, and it is always normal mode.
1. Connect USB. The updater shows "Connect USB" and exposes a FAT12 RAM disk.
2. Copy only stock `UpdOcta.dat` into the root of the empty disk. Copy it once: do not delete and re-copy, and do not copy other files. This keeps the file contiguous.
3. Eject/unplug as the screen asks ("Put file, and Unplug"). Wait for "Check file", then "Erase", then "Writing...", then "Update Complete". Do not cut power while this runs.
4. Power-cycle. The loader screen must show "MAIN V1.51(017B)". If the updater comes back instead, the write failed silently; repeat from step 1.

**Case B: the patched main boots far enough that the key scan ran** (the main UI appears, or it crashes later).
1. Power off. Hold the normal combo keys {6,1}, and **never key 0** (see the warning below). Power on and keep holding until the updater screen appears.
2. Check that the header has no "[ALL]". If "[ALL]" shows, power off without copying anything.
3. Continue with Case A, steps 1-4.

**Case C: the loader screen shows "MAIN Vx" and then the unit hangs or crashes before the scan.** Neither button combo nor power cycling helps. Status: INFERRED.
- Recovery means rewriting flash 0x20000-0xEFFFF, or the whole chip from stock UpdOcta.dat, which maps 1:1, using one of these:
  - BF52x JTAG (ICE-100B/gnICE plus a flash driver), or
  - an in-circuit or off-board programmer on the parallel NOR chip.
- Whether the board has a JTAG header, and whether BMODE straps allow a UART boot, are both UNKNOWN; check the board before the first patched flash.
- A cheap partial way out: none known. No input is read before main runs.

**Warning:** in Cases A and B, never use the {6,0} combo. The rows are checked in order, and `{6,0,0}` (W=0x34, [ALL]) comes before `{6,1,1}`. So holding 6+0+1 together, or any overlap that includes key 0, selects [ALL], which rewrites sector 0 (booter and loader). Holding 4+2+0 or 4+3+0 selects modes 1 or 2 instead, because those rows are checked first. Holding 0+1 alone gives a factory reset of the settings.

## 4. Settings storage, and whether a patched main can damage it

**Location.** Settings live in the last 64 KB sector, 0xF0000-0xFFFFF (sector index 18). In UpdOcta.dat this range is 0xDEAD filler, and the updater never writes it. Status: CONFIRMED.

| Range | Contents | Writer |
|---|---|---|
| 0xF0000-0xF000B | 12-byte ID record, "ISRL" + 8 hex chars | 0x31d47c, only from test mode (0x35b1b0, case R1==2). Read at boot (0x31bd52/5c/64) |
| 0xF0040-0xF0D5B | primary settings block, 0xD1C bytes: magic 0x55AAFF00, then version 0x1F | saved at power-off (0x3192b8 -> 0x31d160) and at 0x31e3a4 |
| 0xF0D5C-0xF1A77 | full backup copy (same layout) | save2 0x31d404 (0x31e3aa); load2 0x31d334; checked at 0x30f4b6 |

(The earlier "8-byte trailer" claim was wrong: 0xF0D5C holds a full backup copy, and the 8 bytes are just its magic and version header.)

**Who writes this sector in main.**
- Main's only flash writer is the "extmem" device (ops at 0x419f5c, write 0x367c68). It does a read-modify-write of a whole sector: erase 0x368c98, program 0x368d30, verify 0x368a2e, 4 tries.
- The only code that opens it is 0x31d074-0x31d70a, and every seek there lands in 0xF0000-0xF1A77. Status: CONFIRMED.
  - All 102 open() call sites were resolved.
  - The erase/program routines have no other callers and no pointer references.
- Main has no write path to 0x0-0xEFFFF. "OCTA-CAPTURE SYSTEM UPDATE" is only a USB descriptor string (PID 0x0125). Status: CONFIRMED.

**Boot behaviour.**
- The boot path is 0x319760 `CALL 0x31cec8`, then 0x319766 `CALL 0x31d074`, then 0x319772.
- If the settings fail to load (open error, wrong magic, wrong version, or mode 4), main writes defaults (0x31977e -> 0x31d27c) and invalidates the backup header with 0xFF.
- A power cut during a save can only damage sector 18. That gives defaults on the next boot, not a brick. The ID record would stay lost, because it is only rewritten in test mode. Status: INFERRED from confirmed parts.

**Risk from a patched main:**
- **Low** if the patch leaves the settings table, the length and the "extmem" callers alone.
- If the patch changes the parameter table or the version word 0x1F, the first boot just resets settings to defaults (harmless).
- **Length overflow.** The length check at 0x31cfc6 does NOT cap the length; an oversized table only prints a warning. If len > 0x7FE0, the backup at 0xF0040+len runs past 0xFFFFF.
  - The driver's write loop never clamps (0x367c90-0x367d02).
  - Past the chip end, the sector lookup returns index 19 with size 0, and the code overflows the heap. Expect a crash during save, after the scan, so it is recoverable. Not tested.
- **Bad seek or direct call.** The driver's sector table covers the whole chip, and `lseek(0)` is accepted. A patch that breaks a seek offset, or that calls 0x368c98/0x368d30 directly, could erase sector 0. **That is a hard brick that no combo can fix.**

## 5. Residual risks, ranked

1. **The patched main hangs or crashes before key scan 0x31bbf6, with a good checksum.** This is an unrecoverable brick without JTAG or a programmer. It covers:
   - the crt/meminit code (0x300100-0x30021a, meminit 0x30f044);
   - kernel and hardware init (0x32a880, 0x329704...);
   - the task's own pre-scan calls (0x300d84, 0x30e5b4, 0x300be0, 0x325a76, 0x34f31c, 0x329b0c);
   - any ISR or task that runs before or alongside them;
   - a mis-sized LZSS stream or unpack size that corrupts the program image.
2. **The patch reaches extmem with a bad offset, or calls the erase/program routines directly.** That can erase sector 0, which holds the booter and the loader: hard brick.
3. **Entering the updater in [ALL] mode** (key 0 involved, or the wrong combo). It rewrites sector 0, and a power cut or silent write failure there is a hard brick. The key-to-button mapping and the key polarity are still unknown.
4. **Silent write failure in normal mode.** "Update Complete" is shown even after a flash error. This is mitigated, because the main checksum breaks and the loader auto-falls back to the updater, unless the corruption happens to keep the 16-bit sum (very unlikely).
5. **A fragmented RAM-disk copy flashes garbage.** This is mitigated the same way, by the checksum fallback. A garbage image whose 16-bit sum matches by chance is roughly a 1/65536 event, and it would crash in crt, giving risk 1.
6. **The settings length grows past 0x7FE0.** A crash during save, likely recoverable via the combo, but untested.
7. **Settings format or version change.** The user's settings reset to defaults. Harmless.
8. **Power cut during a normal update.** Main is half-written, so the checksum is bad, and the auto-fallback to the updater recovers it. The updater region is untouched. INFERRED, but every link in it is CONFIRMED.

## 6. Rules the patch builder must enforce

1. **Only change bytes in 0x20000-0xEFFFF.** Bytes 0x0-0x1FFFF and 0xF0000-0xFFFFF must stay byte-identical to stock. That includes the 0xAD7BD006 magic at offset 0 and the 0xDEAD filler. The output must be exactly 1,048,576 bytes.
2. **Recompute the main sum.** It is the 16-bit LE byte-sum of [0x20000, 0xEFFFE), stored at 0xEFFFE/0xEFFFF. Verify it independently after writing. Also re-verify that the stock updater sum over [0x4000,0x1FFFE) still matches 0x1FFFE/F; if it does not, the fallback is dead.
3. **Round-trip the LZSS.**
   - Decompress the built stream with an emulation of the loader routine itself (0xffa0811c semantics: 0xFEE-byte zero-filled ring, r=0xFEE, LSB-first flags, length = nibble+3, stop on a literal after the output is full).
   - Require a byte-exact match with the intended main.bin.
   - The BE32 size at 0x20100 must be >0 and equal len(main.bin).
   - The stream must end before 0xEFFFC.
4. **Keep 0xEFFFC = 0x017B and the version word at 0x200F8, unless you mean to change them.** Optionally, set a distinct build word so the loader's "MAIN Vx.xx(XXXX)" line shows which main is running. Nothing found compares the build word, but that is INFERRED.
5. **Forbidden-zone check.** Refuse any patched byte (in unpacked main.bin coordinates) inside the pre-scan code:
   - crt and stubs 0x300100-0x30021a and meminit 0x30f044 plus its table;
   - kernel and hardware init 0x329704-0x32a9ff;
   - task 0x31bba0-0x31bd46 itself;
   - the pre-scan callees 0x300d84, 0x30e5b4, 0x300be0, 0x325a76, 0x34f31c, 0x329b0c, plus everything they call;
   - the "sw" driver (0x3666d8, 0x366aa4, 0x366764-0x3667ec);
   - interrupt and exception vectors, and the reboot path 0x32a856/0x32a8cc/0x32a946.

   The builder should compute the call closure from main.dis instead of trusting this list. Where possible, put new code in code that only runs after 0x31bd74 (mode 0).
6. **Leave the meminit table and the data/bss layout alone,** unless the builder re-parses the table and proves no record overlaps. Hook by appending code only where the meminit and bss maps show free space.
7. **No new flash access.** Refuse patches that reference the "extmem" string (0x435dd4 / 0x41c4c4), the extmem ops table 0x419f5c, 0x367ae4-0x368e88, the flash table 0x419fe4/0x419fe8, or any 0x2000xxxx store. Settings code stays stock, or the settings length stays <= 0x7FE0.
8. **Do not touch the combo tables** 0x435d50-0x435dbf or the handler table 0x435db0. The {6,1,1} -> 0x33 recovery row must stay exactly as it is.
9. **Emit a manifest:** the patched ranges, both sums, the LZSS round-trip result, and the forbidden-zone report. Refuse to output the .dat if any check fails.
10. **Before the first patched flash,** confirm the {6,1} combo on stock (section 3, step 0), and check the board for a JTAG header or NOR clip access as a last resort.

TL;DR
- Normal update = main only; settings sector and boot sectors are safe.
- Bad checksum = auto-recovers. Crash after the key scan = recover with the {6,1} combo. Crash before the key scan = brick.
- So: never patch pre-scan code, never touch extmem, never use [ALL] or key 0, and test the combo on stock first.
