/* LDAC add-on: C side of the four stock patches (see docs/LDAC_PORT.md, "Diseño de ganchos").
 *   H1 0x0C0BF8FA  bl stock_aac_setup      -> bl ldac_boot_hook
 *   H2 0x0C0C027A  cmp.w r8,#0             -> bl ldac_cfg_hook        (hooks_asm.S)
 *   H3 0x0C127876  bl memcpy (SBC table)   -> bl ldac_dec_copy_hook
 *   H4 0x0C0C00AC  cmp r3,#2; bne.w 0318   -> bl ldac_cfginit_hook; nop (hooks_asm.S)
 *   H5 0x0C0BFCA8  ldrb r3,[r5]; cmp r3,#2 -> bl ldac_open_hook       (hooks_asm.S)
 *   H6 0x0C0AD072  subs r3,#0x80; cmp r3,#0x19 -> bl ldac_dbg_hook   (hooks_asm.S)
 * LDAC is marked by dev_ctx+0x4a == 0xFF, which the stock TWS sync copies to the peer earbud. */
#include <stdint.h>
#include <stddef.h>
#include "stock.h"
#include "addon.h"

#define CODEC_SBC     0x00
#define CODEC_VENDOR  0xFF
#define LDAC_SEP_ID   1   /* "priority" arg: SBC 2, AAC 3 in the stock; 1 stays inside any range the stock already uses */

/* Sony LDAC codec information element: vendor 0x0000012D, codec 0x00AA, then
 * sampling frequency (0x20 44.1k, 0x10 48k, 0x08 88.2k, 0x04 96k, ...) and
 * channel mode (0x04 mono, 0x02 dual, 0x01 stereo).
 * First version: 44.1/48 kHz, stereo only, so the TWS peer can derive the whole decoder
 * configuration from the synced sample-rate flag. */
#define LDAC_FS_44100 0x20
#define LDAC_FS_48000 0x10
#define LDAC_CM_STEREO 0x01
const uint8_t ldac_caps[8] __attribute__((aligned(4))) = {
    0x2D, 0x01, 0x00, 0x00, 0xAA, 0x00, LDAC_FS_44100 | LDAC_FS_48000, LDAC_CM_STEREO,
};

/* SEP codec descriptor, same layout as the stock SBC/AAC ones (0x2008F5C8/0x2008F5BC):
 * +0 codec type, +1 element length, +2 elements pointer (unaligned), +0xA 1. Must stay in RAM. */
struct sep_desc {
    uint8_t codec_type;
    uint8_t elem_len;
    uint8_t elements[4];   /* unaligned pointer, written bytewise */
    uint8_t pad[4];
    uint8_t flag_a;
    uint8_t pad2;
};
static struct sep_desc ldac_desc;   /* .bss in the RAM gap */

static int is_sony_ldac(const uint8_t *e)
{
    return e && e[0] == 0x2D && e[1] == 0x01 && e[2] == 0x00 && e[3] == 0x00 &&
           e[4] == 0xAA && e[5] == 0x00;
}

/* Linker-provided bounds of the RAM gap image (link.ld). */
extern uint8_t __gap_data_start[], __gap_data_end[], __gap_data_load[];
extern uint8_t __gap_bss_start[], __gap_bss_end[];

static void ram_init(void)
{
    uint8_t *d = __gap_data_start;
    const uint8_t *s = __gap_data_load;
    while (d < __gap_data_end) *d++ = *s++;
    for (d = __gap_bss_start; d < __gap_bss_end; d++) *d = 0;
}

/* H1: called instead of the AAC setup during per-device A2DP init (r0 = device). */
void ldac_boot_hook(int dev)
{
    stock_aac_setup(dev);
    if (dev == 0)
        ram_init();            /* the gap is not zeroed by the stock crt0 */
    uint8_t *ctx = stock_dev_ctx(dev);
    if (!ctx)
        return;
    void *stream = *(void **)(ctx + 0x34);
    if (!stream)
        return;
    uintptr_t caps = (uintptr_t)ldac_caps + 0x20000000u;   /* data alias, like the SBC caps */
    ldac_desc.codec_type = CODEC_VENDOR;
    ldac_desc.elem_len = sizeof ldac_caps;
    ldac_desc.elements[0] = (uint8_t)caps;
    ldac_desc.elements[1] = (uint8_t)(caps >> 8);
    ldac_desc.elements[2] = (uint8_t)(caps >> 16);
    ldac_desc.elements[3] = (uint8_t)(caps >> 24);
    ldac_desc.flag_a = 1;
    /* Fails cleanly ("a2dp_register_sep no sep resource") if no slot is free: LDAC is then just
     * not advertised. 3 slots per stream, SBC+AAC use 2. */
    stock_btif_a2dp_register(stream, 1, &ldac_desc, 0, LDAC_SEP_ID,
                             (void *)((uintptr_t)stock_a2dp_callback | 1u));
}

/* H2 (from hooks_asm.S): stream configuration for a vendor codec.
 * codec = {u8 type, u8 len, u8 *elements (unaligned)}, ctx = device context. */
uint8_t ldac_cfg_flag;     /* 1 after an LDAC stream configuration, 0 after an SBC one */
uint8_t ldac_swap_mode;    /* diagnostics: 1 = H3 selected LDAC via dev ctx, 2 = via ldac_cfg_flag */

void ldac_cfg_apply(const uint8_t *codec, uint8_t *ctx)
{
    const uint8_t *e;
    if (codec[0] == CODEC_SBC) {
        ldac_cfg_flag = 0;
        return;
    }
    __builtin_memcpy(&e, codec + 2, sizeof e);
    if (codec[1] < 8 || !is_sony_ldac(e))
        return;                                   /* unknown vendor codec: stock behaviour */
    uint8_t fs = e[6] & (LDAC_FS_44100 | LDAC_FS_48000);
    if (fs != LDAC_FS_44100 && fs != LDAC_FS_48000)
        return;                                   /* not a configuration we advertised */
    ctx[0x3f] = fs;          /* SBC sample-rate flag encoding happens to match LDAC's */
    ctx[0x40] = 0x10;        /* 16-bit */
    ctx[0x42] = 2;           /* stereo */
    ctx[0x4a] = CODEC_VENDOR;
    ldac_cfg_flag = 1;
}

/* H4 (from hooks_asm.S): the earbud initiates the configuration and the remote SEP is a vendor
 * codec. remote = remote codec caps {type, len, elements*}, cfg = local config codec struct whose
 * elements pointer (cfg+2) points to a zeroed 10-byte buffer (0x0C0C017C). */
void ldac_cfginit_apply(const uint8_t *remote, uint8_t *cfg)
{
    const uint8_t *e;
    uint8_t *out;
    __builtin_memcpy(&e, remote + 2, sizeof e);
    __builtin_memcpy(&out, cfg + 2, sizeof out);
    if (remote[1] < 8 || !is_sony_ldac(e) || !out)
        return;
    uint8_t fs = e[6] & ldac_caps[6];
    uint8_t cm = e[7] & ldac_caps[7];
    if (!fs || !cm)
        return;
    for (int i = 0; i < 6; i++) out[i] = ldac_caps[i];
    out[6] = (fs & LDAC_FS_48000) ? LDAC_FS_48000 : LDAC_FS_44100;
    out[7] = LDAC_CM_STEREO;
}

/* H3: replaces memcpy(ctx_decoder, sbc_decoder_table, 0x70) in a2dp_audio_init's SBC branch. */
void *ldac_dec_copy_hook(void *dst, const void *src, uint32_t n)
{
    uint8_t *ctx = stock_dev_ctx(stock_cur_a2dp_dev());
    ldac_swap_mode = 0;
    if (ldac_decoder_table_ready()) {
        if (ctx && ctx[0x4a] == CODEC_VENDOR) ldac_swap_mode = 1;
        else if (ldac_cfg_flag) ldac_swap_mode = 2;
    }
    if (ldac_swap_mode)
        src = ldac_decoder_table();
    uint8_t *d = dst;
    const uint8_t *s = src;
    while (n--) *d++ = *s++;
    return dst;
}

/* H6 (from hooks_asm.S): OTA SPP opcode 0xA5 'B' 'E' 'S' 'T' -> reply 0xA6, size, ldac_stats.
 * Sent only through the stock "legacy" transmit path used by ota_control_image_apply_rsp
 * (0x0C0AC2C8): role byte 0x2008A060 == 2, env 0x2008A844, env+0xB0 != 1 (not TLV), transmit
 * function at env+0x98 (buf, len). Any other state: no reply, nothing touched. */
#define OTA_ROLE   (*(volatile uint8_t *)0x2008A060u)
#define OTA_ENV    ((volatile uint8_t *)0x2008A844u)
extern uint8_t ldac_cfg_flag, ldac_swap_mode;

void ldac_dbg_request(const uint8_t *pkt, uint32_t len)
{
    (void)len;
    if (!pkt || pkt[1] != 'B' || pkt[2] != 'E' || pkt[3] != 'S' || pkt[4] != 'T')
        return;
    if (OTA_ROLE != 2 || OTA_ENV[0xB0] == 1)
        return;
    void (*tx)(uint8_t *, uint32_t);
    __builtin_memcpy(&tx, (const void *)(OTA_ENV + 0x98), sizeof tx);
    if (!tx)
        return;
    uint8_t buf[2 + sizeof(ldac_stats_t)] __attribute__((aligned(4)));
    const uint8_t *s = (const uint8_t *)ldac_stats_get();
    buf[0] = 0xA6;
    buf[1] = s ? sizeof(ldac_stats_t) : 0;   /* 0: no LDAC stream open */
    for (uint32_t i = 0; s && i < sizeof(ldac_stats_t); i++) buf[2 + i] = s[i];
    if (s) {
        buf[2 + 2] = ldac_swap_mode;   /* ldac_stats_t.swap_mode */
        buf[2 + 3] = ldac_cfg_flag;    /* ldac_stats_t.cfg_flag */
    } else {
        buf[2] = ldac_swap_mode;
        buf[3] = ldac_cfg_flag;
    }
    tx(buf, s ? sizeof buf : 4);
}
