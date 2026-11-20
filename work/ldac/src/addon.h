/* LDAC add-on internal interfaces. */
#ifndef LDAC_ADDON_H
#define LDAC_ADDON_H
#include <stdint.h>
/* The 0x70-byte stock decoder table for LDAC (see LDAC_PORT.md "Tabla del decoder"). */
const void *ldac_decoder_table(void);
/* 0 until the decoder implementation is complete: H3 then keeps the stock SBC table. */
int ldac_decoder_table_ready(void);
/* Statistics read by the PC over the OTA SPP channel: request A5 'B' 'E' 'S' 'T',
 * reply A6, sizeof(ldac_stats_t), then the struct (little endian). Written by the audio thread
 * only (ldac_decoder.c), copied without locking by the OTA thread (hooks.c): a torn read only
 * mixes two consecutive snapshots. Per-stream fields are reset by ldac_init; inits/deinits are
 * cumulative since boot. Times use the stock system timer (0x40002004, down-counter, negated);
 * its rate is calibrated on the PC from req_samples / sample_rate. */
#define LDAC_STATS_VER 7
typedef struct {
    uint8_t ver, pool, swap_mode, cfg_flag;   /* pool: 1 A2DP heap, 2 syspool, 3 none */
    uint32_t sample_rate;
    uint16_t inits, deinits;
    uint32_t decode_calls, frames, bad_frames, underflows;
    uint32_t req_samples;                      /* stereo samples requested by the framework */
    uint32_t t_first, t_last, t_busy;          /* first/last decode entry; ticks inside decode */
    uint16_t t_call_max, t_frame_max;          /* ticks: longest decode call / ldacBT_decode */
    uint32_t store_calls, store_frames;
    uint16_t mem_drops, mtu_limits, list_max, list_now;
    uint16_t sync_calls, sync_fails, disc_samples_calls, disc_packet_calls;
    uint32_t heap_free_init, heap_free_min, syspool_free_init;
    /* framework playback-speed factor (stream_info.factor_reference, ctx+0xc; 1.0 nominal) as
     * float bits: last seen at a decode call, min, max; dest_packet_mut (ctx+0x16) last seen */
    uint32_t factor, factor_min, factor_max;
    uint16_t dest_mut;
    uint16_t ram_err;          /* RAM payload loader: 0 ok, 1 magic, 2 checksum, 3 heap, 4 address, 5/6 relocation, 7 exports */
    /* gaps between decode entries (ticks): max, count > 1.5 periods; calls longer than one period.
     * Period = request size / sample rate (the DMA half-buffer). */
    uint16_t gap_max, late_gaps, slow_calls;
    uint16_t cpu_loop_ticks;   /* ticks for 400000 `subs; bne` iterations at init: CPU speed */
    uint32_t t_frame_sum;      /* ticks inside ldacBT_decode (part of t_busy) */
    uint32_t ram_base;         /* A2DP-heap block running ldac_lib (data alias), 0: none */
    uint16_t cpu_loop_run;     /* ticks for 100000 loop iterations, measured in store every 256 calls */
    uint16_t cpu_loop_runs;    /* number of such measurements */
} ldac_stats_t;
_Static_assert(sizeof(ldac_stats_t) == 120, "stats layout is shared with work/tools/ldac_stats.py");
/* Statistics of the open LDAC stream, NULL when none (ldac_decoder.c). */
const ldac_stats_t *ldac_stats_get(void);
#endif
