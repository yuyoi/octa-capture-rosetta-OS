# Rosetta Lab: the OCTA-CAPTURE test backend

A small SysEx backdoor in Rosetta builds that lets the PC read, write and run code on the live unit over USB. There's no reflash per experiment. The PC side is `tools/octa_lab.py`; the firmware side is `patches/lab1.S`.

## Transport
- **Port:** the OCTA's **CTRL** USB-MIDI port (USB cable 1; cable 2 also works), the same one the Roland Control Panel uses. Close the Control Panel app while using the lab: Windows MIDI ports usually allow one program at a time.
- **Isolation:** the hook sits in the firmware's SysEx dispatcher (task 9, 0x369468), which only ever sees SysEx from cables 1 and 2. The **MIDI** port (cable 0: DIN in/out, used by the S2000 lab with `F0 7D 53`) never reaches it. Lab messages (`F0 7D 4F ...`) are hidden from Roland's listeners; every other SysEx passes through untouched.
- **Replies** are sent as USB-MIDI packets on the cable the request came in on, through the firmware's packet sender 0x36bcf4.
- **Request:** `F0 7D 4F <cmd> <seq> <payload nibbles> F7`
- **Reply:** `F0 7D 4F <cmd|0x40> <seq> <payload nibbles> F7`
- **Payload encoding:** 4-bit nibbles, high nibble first. Request fields are big-endian hex numbers of fixed width. Reply payloads are byte streams (memory bytes in memory order, or big-endian numbers).

## Commands
| cmd | name | request fields (nibbles) | reply |
|---|---|---|---|
| 01 | PING | - | ver u16, vars u32, upload area u32 |
| 02 | PEEK | addr 8, n 2 (1-64), width 1 (1/2/4) | n bytes |
| 03 | POKE | addr 8, n 2, width 1, n bytes as 2 nibbles each | - |
| 04 | CALL | addr 8, r0 8, r1 8, r2 8 | R0 u32, cycles u32 |
| 05 | FILL | addr 8, n 8, word 8 | - |
| 06 | CHECK | addr 8, n 8, word 8 | mismatching words u32, first bad address u32 |
| 07 | SUM | addr 8, n 8 | 32-bit sum of the words, u32 |
| 08 | FLUSH | addr 8, n 8 | - (IFLUSH over the range, so stored code becomes executable) |

- **Width:** sets the access size for PEEK/POKE. Memory-mapped registers must be read with their own width: 2 for most system MMRs, 4 for core MMRs.
- **FILL/CHECK/SUM** work on whole 32-bit words, with addr and n multiples of 4. They run on the unit, so a whole-SDRAM check is one message.
- **CALL** runs `addr(r0, r1, r2)` in the MIDI task's context with the Blackfin C calling convention. Upload code with POKE, FLUSH it, then CALL it. It must return; it must not block the MIDI task for long.

## Safety
- The backend is armed only after the power-on button scan, so update mode always stays reachable.
- **A bad address can fault the CPU.** Then the unit hangs and needs a power cycle. Nothing is written to flash, so a power cycle always recovers. `octa_lab.py` refuses addresses outside the known memory map unless you pass `--force`.
- **memprobe** first checks the proven SDRAM geometry (re/RAM_MAP.md: 2 MB real, A20/A21 mirrors, 0x800000+ forbidden) with one marker in the free upload area at 0x3FFFFC: it must show at its bank-0 mirrors 0x0/0x1/0x2FFFFC and not in bank 1. Only then does it fill guard patterns over the free areas. **Never touch 0x800000-0xFFFFFF**: that killed lab1-lab3 (reads there desync the SDRAM controller and later code fetches get garbage). Lab >= 1.2 refuses it on the unit too.

## How it's built
- `patches/lab1.S`: the hooks and the meter, plus `lab_entry`, a slim trampoline that keeps task 9's tight stack.
- `patches/lab.c`: the command processor, built with bfin-elf-gcc 13.3 on newpc.
- `patches/asm.py lab1 lab.c` builds them; `tools/build_dsp.py` applies them with stock-byte checks and appends the backend after the init table at 0x3A3000.
- `patches/tests/`: a host build of `lab.c` (fake memory and USB) driven by the real `octa_lab.py` `Lab` class. Its 17 checks cover every command, the address guard, Roland pass-through and the cable handling.
