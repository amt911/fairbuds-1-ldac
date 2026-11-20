/* LDAC add-on blob for Fairbuds 1 stock 6.5.
 * Code/rodata: appended after the original image, executed from the FLASHX alias (0x0C......),
 *   like the stock. BLOB_BASE is passed with --defsym (= 0x0C020000 + aligned original size).
 * RAM: the unreferenced padding between .bss end (0x200BC734) and the newlib heap (0x200BC800);
 *   not zeroed at boot -> ldac_addon_init() copies .data and zeroes .bss before anything else. */
MEMORY {
  BLOB (rx)  : ORIGIN = BLOB_BASE, LENGTH = 0x48000
  GAP  (rw)  : ORIGIN = 0x200BC734, LENGTH = 0x200BC800 - 0x200BC734
}
INCLUDE stock_syms.ld
SECTIONS {
  .text : {
    KEEP(*(.entry))
    *(.text .text.*)
    *(.rodata .rodata.*)
    . = ALIGN(4);
  } > BLOB
  .data : { __gap_data_start = .; *(.data .data.*) . = ALIGN(4); __gap_data_end = .; } > GAP AT > BLOB
  __gap_data_load = LOADADDR(.data);
  .bss (NOLOAD) : { __gap_bss_start = .; *(.bss .bss.* COMMON) . = ALIGN(4); __gap_bss_end = .; } > GAP
  /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment) }
}
