/* lab1: hooks + overlay code at fixed stock addresses, the lab backend appended after the init table.
 * LAB_BASE = 0x300100 + (splash main.bin length rounded up to 0x100) = 0x3a3000 (tools/build_dsp.py checks it). */
SECTIONS
{
  .ra    0x30de0e : { lab1.o(.ra) }
  .rb    0x30de98 : { lab1.o(.rb) }
  .h_arm 0x31bd7a : { lab1.o(.h_arm) }
  .h_ui  0x359a16 : { lab1.o(.h_ui) }
  .h_gb  0x30de0c : { lab1.o(.h_gb) }
  .h_ga  0x30de96 : { lab1.o(.h_ga) }
  .h_midi 0x36949a : { lab1.o(.h_midi) }
  .lab   0x3a3000 : { lab1.o(.lab) *(.text) *(.text.*) *(.rodata) *(.rodata.*) *(.data) *(.data.*) *(.bss) *(.bss.*) *(COMMON) }
  /DISCARD/ : { *(.comment) *(.note*) }
}
