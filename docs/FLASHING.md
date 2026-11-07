# Flashing Rosetta on the OCTA-CAPTURE

**TL;DR**
- **Build:** `python tools/build_rosetta.py octa_sys_v151.zip`, using Roland's own v1.51 download.
- **Flash:** with Roland's own update procedure. Hold **[▶] in the PRE-AMP section** and **push the CURSOR/VALUE knob**, then power on.
- **Before copying:** the updater screen must **not** show `[ALL]`.
- **Back to stock:** flash Roland's original `UpdOcta.dat` the same way.

## 1. Build
1. Download Roland's **OCTA-CAPTURE System Program Ver.1.51** (`octa_sys_v151.zip`) from Roland's support site.
2. Run `python tools/build_rosetta.py octa_sys_v151.zip`. You need Python 3 with `numpy` and `Pillow`.
3. It writes `UpdOcta.dat` next to the zip. For the default splash, it should say `= the hardware-tested build`.

The script refuses to build from any other file, and it checks the result:
- the checksums;
- that it unpacks back to the patched program;
- that no byte outside the main program changed.

## 2. Enter update mode
1. Power off and unplug USB.
2. Hold the **[▶] button** in the PRE-AMP section and **push the CURSOR/VALUE knob**: the right-hand knob, marked SELECT (PUSH). Power on.
3. Keep holding until the screen says `OCTA-CAPTURE updater`.
4. **Check the header.** It should read `ver:1.00/0141` with **no `[ALL]` before it**. If `[ALL]` shows, power off and copy nothing: that mode rewrites the boot sector too.

## 3. Flash
1. Plug in USB. The screen says `Put file, and Unplug`, and a small removable disk appears.
2. Copy **only** `UpdOcta.dat` onto it, **once**. Don't delete and re-copy.
3. Unplug USB. It shows `Wait, Writing`, then `Update Complete`. **Don't cut the power while it writes.**
4. Power-cycle. First the stock loader logo, then the Rosetta splash, then the normal OCTA screens.

## If something goes wrong
- **The updater screen comes up by itself:** the write was bad and the checksum caught it. Copy Roland's original `UpdOcta.dat`, unplug, and power-cycle.
- **It runs but looks wrong:** enter update mode as in step 2 and flash Roland's original file.
- **Never hold any other button combination at power-on.** Another combo selects `[ALL]` mode, which also rewrites the boot sector. Two key presses (0 + 1 in the code) do a factory reset of your settings.

## The one real risk
The button combo is read by the main program itself. So a main program with a good checksum that freezes **before** the button scan can't be fixed with buttons. It would need JTAG or a flash programmer on the board. The splash patch only touches the picture and the drawing of one startup screen (9 code bytes). It was checked offline, and it boots on real hardware. Details: [re/FLASH_SAFETY.md](../re/FLASH_SAFETY.md).
