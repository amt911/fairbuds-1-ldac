/* LDAC library executed from RAM (the "payload").
 * ldac_lib + its helpers are linked separately at LDAC_RAM_VBASE, a virtual address more than
 * 16 MB away from flash and RAM, so ld.lld routes every external call through an absolute thunk
 * and the code depends on its position only through R_ARM_ABS32 words (built with -mno-movt;
 * tools/mkpayload.py rejects anything else). At stream init the flash decoder copies the image
 * into an A2DP-heap block and relocates those words: targets in the code part go to the RAMX
 * (instruction) alias of the block, everything else (rodata, data, bss) to the data alias.
 * Why: from flash (XIP) ldacBT_decode took ~43 % of real time whatever the CPU clock (104 or
 * 208 MHz), missing ~2 DMA periods per second (FINDINGS.md, v6a..v6h). */
#ifndef LDAC_RAM_H
#define LDAC_RAM_H
#include <stdint.h>
#include "ldacBT.h"

#define LDAC_RAM_MAGIC  0x4D41524Cu   /* "LRAM" */
#define LDAC_RAM_VBASE  0x10000000u

/* Filled by the flash side after relocation (ldac_lib's allocator hooks). */
struct ldac_ram_imports {
    uint32_t (*free_size)(void);
    int (*get_buff)(uint8_t **buff, uint32_t size);
};

/* First bytes of the payload (section .exports). */
struct ldac_ram_exports {
    uint32_t magic;
    HANDLE_LDAC_BT (*get_handle)(void);
    int (*init_decode)(HANDLE_LDAC_BT h, int cm, int sf, int var0, int var1, int var2);
    int (*decode)(HANDLE_LDAC_BT h, unsigned char *bs, unsigned char *pcm, LDACBT_SMPL_FMT_T fmt,
                  int bs_bytes, int *used_bytes, int *wrote_bytes);
    void (*free_handle)(HANDLE_LDAC_BT h);
    struct ldac_ram_imports *imports;
};

/* Image in flash (tools/mkpayload.py): this header, then `load_size` bytes (code, rodata,
 * data) to copy to the block, then `nrel` u32 offsets of ABS32 words to relocate. The block
 * needs load_size + bss_size bytes; offsets < code_size are code. */
struct ldac_ram_image {
    uint32_t magic;
    uint32_t code_size;   /* [0, code_size): .exports + .text */
    uint32_t load_size;   /* + .rodata + .data, multiple of 4 */
    uint32_t bss_size;
    uint32_t nrel;
    uint32_t crc;         /* sum of the load bytes and relocation offsets (u32 words) */
};
#endif
