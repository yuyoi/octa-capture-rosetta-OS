/* STRIP page as an upload blob for live tests: everything at the lab upload area 0x3B0000 (tools/octa_lab.py page load). */
SECTIONS
{
  .page 0x3B0000 : { *(.text) *(.text.*) *(.rodata) *(.rodata.*) *(.data) *(.data.*) *(.bss) *(.bss.*) *(COMMON) }
  /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame) *(.eh_frame_hdr) *(.debug*) }
}
