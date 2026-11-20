/* LDAC payload executed from RAM (src/ldac_ram.h). Linked at a virtual base far from flash
 * (0x0C......) and RAM (0x2......./0x00......) so every external call gets an absolute thunk;
 * relocated at run time by the flash decoder from the R_ARM_ABS32 list made by mkpayload.py. */
INCLUDE stock_syms.ld
SECTIONS {
  . = 0x10000000;
  .text : {
    KEEP(*(.exports))
    *(.text .text.*)
    . = ALIGN(4);
    __ram_code_end = .;
    *(.rodata .rodata.*)
    . = ALIGN(4);
  }
  .data : { *(.data .data.*) . = ALIGN(4); }
  __ram_load_end = .;
  .bss (NOLOAD) : { *(.bss .bss.* COMMON) . = ALIGN(4); }
  __ram_end = .;
  /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment) }
}
