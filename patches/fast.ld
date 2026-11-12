/* fast: loader in the SDRAM upload area, audio path linked to free L1 instruction memory (build_fast.py checks the size). */
SECTIONS
{
  .up   0x3B0000   : { fast_loader.o(.text) fast_loader.o(.text.*) fast_loader.o(.rodata) fast_loader.o(.rodata.*) }
  .fast 0xFFA04900 : { fast.o(.fast) fast_strip.o(.text) fast_strip.o(.text.*) fast_strip.o(.rodata) fast_strip.o(.rodata.*) }
  /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame) *(.eh_frame_hdr) *(.debug*) }
}
