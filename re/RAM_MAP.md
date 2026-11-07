# OCTA-CAPTURE RAM map (stock v1.51 main)

How it was checked:
- the SDRAM controller setup in the booter;
- the VisualDSP init table, emulated (tools/build_splash_main.py);
- every exact `.L`/`.H` address pair in main.dis and in the L1 code. Plain grep on objdump's annotations gives false hits, because objdump merges a new half with a stale register value.

## TL;DR
- **SDRAM: the controller is set for 16 MB** (CONFIRMED setting). The program uses about **1.3 MB** of it: code 0x300100-0x36e66b, data and bss 0x400000-~0x450000.
- **CORRECTION (CPLB/heap review): 0x44DE6C-0x4FFFFF is VisualDSP heap 0** (heap table 0x43667c = {0x44de6c, 0xb2194, 0}, init 0x325270, malloc in L1 0xffa001c8). The earlier "no heap" was wrong.
- **Free: 0x500000-0xFFFFFF (11 MB) and 0x000000-0x2FFFFF (3 MB)** (INFERRED free, no pointers). That's room for about 55 channel-seconds of delay at 48 kHz (32-bit samples). The dead init table 0x3A2D9C-0x3FFFFF is also free after boot; Rosetta's lab code is appended there, and uploads go to 0x3B0000.
- **L1 (fast on-chip RAM):** ~20 KB of instruction RAM and ~2.9 KB of data RAM are free (INFERRED free, see the table). That's enough for a channel-strip EQ in fast memory.
- **The physical chip size is not proven.** The controller says 16 MB, but a smaller chip would alias. A board photo of the SDRAM chip, or one alias test on hardware, settles it.

## SDRAM (external, 0x00000000-0x00FFFFFF)
| Setting | Value | Meaning |
|---|---|---|
| EBIU_SDGCTL (booter 0x20000110) | 0xe0911149 | timing |
| EBIU_SDBCTL | 0x1 | bank enabled; EBSZ = 000 = **16 MB**; EBCAW = 00 = 8-bit columns (CONFIRMED value, size from the BF52x HRM) |
| EBIU_SDRRC | 0x5f9 | refresh |

| Range | What | Status |
|---|---|---|
| 0x000000-0x2FFFFF | nothing at runtime. At boot the loader unpacks main to 0x300100; the updater is a separate program | INFERRED free (no pointer from code or data) |
| 0x300100-0x36E66B | main .text (450 KB) | used |
| 0x36E66C-0x3FFFFF | nothing (the init table stays in flash/file only) | INFERRED free |
| 0x400000-0x44DE6B | .data/.rodata/zeroed .bss from the init table | used |
| 0x44DE6C-0x4FFFFF | VisualDSP heap 0 (malloc) | used (CONFIRMED) |
| 0x500000-0xFFFFFF | nothing found: 11 MB | INFERRED free |

Things that looked like pointers and weren't:
- 0x800000 / 0x400000 in the crt area are 128x64 clipping constants.
- 0x470798 is a UART divisor constant.
- 0x99ff00, 0x666666 etc. are UI colours from the VS-100 code base.
- The 0x10xxxx-0x2Exxxx words in the data are packed parameter tables.

## L1 on-chip RAM (BF52x)
| Range | Size | Use | Status |
|---|---|---|---|
| 0xFFA00000-0xFFA048BD | 18.6 KB | ISR, worker, drivers | used (init table) |
| **0xFFA048BE-0xFFA07FFF** | **~14 KB** | - | INFERRED free (no exact refs, not in the init table) |
| 0xFFA08000-0xFFA0A733 | 10 KB | audio callbacks (48k/96k/192k chains, comp) | used |
| **0xFFA0A734-0xFFA0BFFF** | **~6 KB** | - | INFERRED free |
| 0xFFA10000-0xFFA13FFF | 16 KB | instruction cache (IMEM_CONTROL = 6) | not usable |
| 0xFF800000-0xFF8034B3 | 13.2 KB | audio frames, filter/comp state, driver vars | used (exact refs end at 0xFF802CB4 plus struct sizes) |
| **0xFF8034B4-0xFF803FFF** | **~2.9 KB** | - | INFERRED free. The earlier "used at 0xFF803808/3B48/3C00" was objdump annotation noise; there's no exact ref |
| 0xFF804000-0xFF807FFF | 16 KB | data cache (DMEM_CONTROL = 0xE, DMC = 11) | not usable |
| 0xFF900000-0xFF903FFF | 16 KB | task stacks (only base refs) | used |
| 0xFF904000-0xFF907FFF | 16 KB | data cache | not usable |
| 0xFFB00000-0xFFB00673 | 1.6 KB | interrupt stacks (IVG7-14 SP reloads at 0x44/0x84/0xC4/0x344/0x3A4), audio state | used |
| 0xFFB00674-0xFFB00FFF | 2.4 KB | boot / kernel stack (crt SP = 0xFFB01000) | used |

## What it means for the roadmap
- **EQ/comp channel strip:** code goes in the free L1 code (14 KB plus 6 KB), and coefficients and state in the free L1 data (2.9 KB). 8 channels × 5 biquads × ~32 B of state and coefficients ≈ 1.3 KB, which fits. Loading code into L1 needs a post-scan copy (MDMA/ITEST); core stores can't write L1 instruction RAM.
- **Delays / looper-style buffers:** SDRAM 0x500000+ (11 MB if the chip really is 16 MB). At 48 kHz with 32-bit samples that's 192 KB per channel-second, so about 55 channel-seconds. At 96 kHz it's about 27.
- **Before using any of it:** run one hardware probe build:
  - **Alias test:** write different markers at 0x700000 and 0xF00000. If they collide, the chip is 8 MB.
  - **Canaries:** fill the free L1 data and a few SDRAM pages with a pattern at arm time, then show the number of changed words on the meter line for a few minutes of normal use.

## CPLBs and faults (from the CPLB review, CONFIRMED)
- ICPLB: L1 code 1 MB; SDRAM 4 × 4 MB cacheable (all 16 MB executable); flash 4 × 1 MB.
- DCPLB: L1 data A/B 1 MB each; SDRAM 4 × 4 MB write-through, supervisor write; flash.
- No CPLB miss handler. EVT3 = `EMUEXCPT; RTX`, so any fault (uncovered address, a normal load from L1 code 0xFFA0xxxx, misaligned access) re-runs forever. The faulting task hangs; a power cycle recovers it.
- Code stored at runtime: SSYNC, then IFLUSH each 32-byte line (not as the last instruction of a hardware loop), then SSYNC; CSYNC.
