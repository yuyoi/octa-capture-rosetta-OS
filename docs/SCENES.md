# Scenes: TEMP + a 16-slot bank on the box (strip block v4, lab 1.8)

User request (2026-11-11): "new menu when DISPLAY is pressed, a reasonable amount of user setting pages, saved on hardware, swapped with programs". Decided with the user: whole-box scenes, 16 slots, "Temp and bank" like a synth, the PC app reads/writes slots.

| | |
|---|---|
| Scene | all 8 strips (every filter, tube, comp setting and flag) + the amp + the box STRIP page's values + 800 bytes the PC app keeps its knob positions in |
| Record | 0xC40 bytes, magic 'RSC1', version 1, CRC32, name 16 chars |
| Where | flash 0xF2000-0xFF040 = record 0 TEMP + 16 bank records, the free tail of the settings sector (Roland's data ends at 0xF1A77, then 0xDEAD factory filler on both units, checked 2026-11-11). The updater never writes this sector, so scenes survive firmware updates. |
| How | only Roland's own "extmem" driver (open/lseek/write/close, as its settings save 0x31d160). One gate, `fl_io()`, refuses any range outside 0xF2000-0xFF040 before opening; lseek must land where asked. The driver rewrites the 64 KB sector on close, which Roland's power-off save also does every time. |
| TEMP | written inside Roland's power-off save: hook `h_save` replaces its close() at 0x31d1cc (stock `CALL 0x3667ec`), so it's one erase. Read back at power-up (page_install -> scene_boot) and applied before the PC connects. Bad CRC = ignored. |
| Recall | the strip mailbox (one filter between two samples, state cleared only for blocks that come on), then the single-word settings, then the flags. No interrupt masking, no added delay. |
| Box menu | DISPLAY short press on HOME (HOME KeyUp vtable word 0x401cdc, stock 0x3139a0 = "not mine"). BANK page = screen 18 mode 2: VALUE/GAIN turn or CH </> = slot, VALUE push = LOAD, GAIN push = WRITE (asks), DISPLAY = HOME. Hold DISPLAY = SYSTEM as stock. |
| PC | lab PING reply + 4 bytes = address of `struct scene_ctl` (scene.h). The PC fills slot (+ rec), then req; the UI tick does the work (flash never from the MIDI task) and sets status/done. Requests: STORE, RECALL, READ, WRITE_RAW (CRC-checked), SAVE_TEMP (app block), NAMES. `loads` counts every recall so the PC can follow box-side loads. |

Risks and what covers them:
- **Wrong address = brick.** One gate with fixed constants, checked on the host: every write in the test run lands in 0xF2000-0xFF040 or Roland's own block; the serial ID and the bytes either side are untouched.
- **Power cut during a WRITE.** Same exposure as Roland's own power-off save: the sector can come back with defaults, and the serial ID record is lost. `backup/` holds both units' sector 18 (2026-11-11) to put it back.
- **Audio during a write.** Code runs from SDRAM and the audio interrupt keeps running, but a glitch during the ~1 s erase isn't ruled out until heard on hardware.

Host test: `patches/tests/test_scene_host.c` (23 checks, run by run_host_test.sh on newpc).
