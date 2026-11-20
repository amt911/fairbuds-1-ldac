/* Stock 6.5 functions and data used by the LDAC add-on.
 * Addresses live in stock_syms.ld (one line of evidence each); this header only gives prototypes.
 * Code runs at the FLASHX alias 0x0C......; data constants are read at 0x2C...... */
#ifndef LDAC_ADDON_STOCK_H
#define LDAC_ADDON_STOCK_H
#include <stdint.h>

/* 0x0C0C3D44: current A2DP device id (0/1, falls back to a global when 0xFF). */
int stock_cur_a2dp_dev(void);
/* 0x0C0BCA00: per-device A2DP context (dev*0x130+base); NULL (and a trace) when dev >= 2.
 *   +0x34 stream object, +0x3f sample-rate flag (SBC encoding 0x20=44.1k 0x10=48k),
 *   +0x40 sample bits (0x10), +0x42 channel mode (1 mono, 2 stereo), +0x4a codec type
 *   (0 SBC, 2 AAC, 0xFF vendor = our LDAC marker; synced to the TWS peer by 0x0C0BF6F8/0x0C0BF714). */
uint8_t *stock_dev_ctx(int dev);
/* 0x0C0C2858: original AAC SEP setup (the call replaced by hook H1). */
void stock_aac_setup(int dev);
/* 0x0C103444: btif_a2dp_register(stream, role(1=sink), codec_desc, 0, sep_id, callback). */
int stock_btif_a2dp_register(void *stream, int role, void *desc, int zero, int sep_id, void *cb);
/* 0x0C0BFBC8: the A2DP event callback used by the SBC/AAC SEPs (passed with the Thumb bit). */
void stock_a2dp_callback(void);

#endif
