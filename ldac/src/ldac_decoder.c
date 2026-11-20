/* LDAC decoder for the stock 6.5 A2DP decoder framework (MCU path, no CP).
 * Port of OpenPineBuds apps/audioplayers/a2dp_decoder/a2dp_decoder_ldac.cpp (MCU path) to the
 * stock structures, checked against the stock SBC decoder (overlay 1, LDAC_PORT.md):
 *   frame entry (0x14): u16 seq @0, u32 ts @4, u16 cur @8, u16 total @0xa, buf @0xc, len @0x10
 *   lastframe info (0x34): SDK layout shifted by a 4-byte field at +0
 *   context: +0x16 u16 dest_packet_mut, +0x90 input list
 *   media header: u16 seq @4, u32 ts @8; payload starts with the 1-byte frame-count header.
 * The framework asks for 16-bit stereo PCM (dev+0x40 = 16) and converts to 24 bits itself.
 * Never asserts: the stock heap/syspool allocators assert when empty, so free space is checked
 * first. Never produces anything but decoded audio or silence (with a fade-in after silence):
 * no diagnostic tones (they were removed after the v4d click incident). */
#include <stdint.h>
#include <stddef.h>
#include "ldac_ram.h"
#include "addon.h"

/* ---- stock framework (addresses in stock_syms.ld) ---- */
void *a2dp_audio_heap_malloc(uint32_t size);
void a2dp_audio_heap_free(void *p);
void a2dp_audio_heap_info(uint32_t *total, uint32_t *used, uint32_t *max_used);
int a2dp_audio_list_append(void *list, void *data);
int a2dp_audio_list_remove(void *list, void *data);
uint32_t a2dp_audio_list_length(void *list);
void *a2dp_audio_list_begin(void *list);
void *a2dp_audio_list_node(void *node);
int a2dp_audio_decoder_internal_lastframe_info_set(void *info);
uint32_t stock_syspool_free_size(void);
int stock_syspool_get_buff(uint8_t **buff, uint32_t size);
int stock_sysfreq_req(uint32_t user, uint32_t freq);

#define NO_ERROR          0
#define DECODE_ERROR     (-1)
#define MEMORY_ERROR     (-3)
#define MTU_LIMITER      (-4)
#define CACHE_UNDERFLOW  (-5)
#define SYNC_ERROR       (-6)

/* Frames in the input list. The SDK limit (200) counts packets; here each list entry is one frame
 * (2 per packet at 48 kHz). With 200 the stock framework, which settles at ~180 frames (~480 ms),
 * hit the limiter on every burst: store -4 -> discards + TWS resync -> decoder restart every 3-9 s
 * (v6a on-device stats, 2026-11-19; FUN_0c127274). ~200 B per frame: 400 frames ~ 80-140 KB of
 * the ~310 KB A2DP heap. */
#define LDAC_LIST_LIMIT      400
#define HEAP_MARGIN          0x800   /* keep this much of the A2DP heap free */
#define LDAC_LIB_NEED        0x3F90   /* measured: ldac_lib init takes 16272 B (emu_decoder.py) */
#define LDAC_LIB_MARGIN      0x400
#define LDAC_FRAMES_RESERVE  0x6000   /* heap kept free for the frame list when ldac_lib uses it */
#define FRAME_PCM_MAX        (256 * 2 * 2)
#define LIB_ALLOCS_MAX 8   /* ldac_lib makes 7 allocations at init (measured); list in blk_t */

typedef struct {
    uint16_t seq; uint16_t pad; uint32_t ts; uint16_t cur, total; uint8_t *buf; uint32_t len;
} frame_t;
typedef struct {
    uint32_t sample_rate; uint8_t num_channels, bits_depth; uint16_t pad;
    uint32_t frame_samples; float factor_reference;
} out_cfg_t;
typedef struct {
    uint32_t unk0; uint16_t seq; uint16_t pad; uint32_t ts; uint16_t cur, total;
    uint32_t frame_samples, list_samples, decoded_frames, undecode_frames, check_sum;
    out_cfg_t stream_info;
} lastframe_t;
typedef struct { uint16_t seq; uint16_t pad; uint32_t ts; uint16_t cur, total; } headframe_t;
_Static_assert(sizeof(frame_t) == 0x14, "frame");
_Static_assert(sizeof(lastframe_t) == 0x34, "lastframe");

/* State lives in the RAM gap (.bss). */
static uint8_t *g_ctx;
static HANDLE_LDAC_BT g_h;
/* One A2DP-heap block per stream: lastframe info (first member: g_last points to it), the
 * statistics read by the PC, and the PCM scratch (ldacBT_decode never writes into the caller's
 * buffer). The RAM gap has no room for more state. */
typedef struct {
    lastframe_t last;
    void *lib_allocs[LIB_ALLOCS_MAX];
    ldac_stats_t stats;
    uint8_t pcm[FRAME_PCM_MAX];
    uint8_t rate_fix_asked;           /* this stream already asked for a rate restart */
} blk_t;
static lastframe_t *g_last;
#define BLK    ((blk_t *)g_last)
#define g_pcm  (BLK->pcm)
#define ST     (&BLK->stats)
#define g_lib_allocs (BLK->lib_allocs)
static uint16_t g_inits, g_deinits;   /* since boot */
static uint8_t g_rate_fixes;          /* consecutive streams that asked for a rate restart */
#define RATE_FIX_MAX 3                /* never loop: give up (silence) after 3 in a row */
static uint32_t g_frame_samples;
/* Where ldac_lib gets its memory: the stock syspool may not have the 16 KB it needs while an
 * A2DP stream is open, so prefer the A2DP heap (allocations tracked and freed in deinit). */
enum { POOL_NONE = 3, POOL_HEAP = 1, POOL_SYSPOOL = 2 };
static uint8_t g_pool;

/* PCM carried over between calls: the tail of the last decoded frame that did not fit. */
static uint16_t g_carry_off, g_carry_len;   /* <= FRAME_PCM_MAX (1024) */
/* Samples left in a fade-in after any silence (underflow, error, start): no steps, no clicks. */
static uint16_t g_fade;
#define FADE_SAMPLES 256
#define LDAC_FREQ_USER 0x1e         /* app_sysfreq_req user only LDAC uses (see ldac_init) */
/* Cache target of the framework's playback-speed tuner (ctx+0x16, dest_packet_mut, in list entries
 * = frames here). The stock caller of a2dp_audio_init (0x0C113540) passes SBC 35, AAC 3 and, for any
 * other codec, 0.0 + 0.0 = 0: the tuner then speeds up (factor 1.00292) forever and drains the list
 * until it underflows (v7e: 140 -> 0 frames in ~150 s). The tuner can speed up (+0.29 %) but barely
 * slow down (0.99992), so a level lost to a radio gap is never refilled: keep the target close to the
 * initial fill (~150 frames). 120 frames = 320 ms at 48 kHz (v7f used 75 = 200 ms). */
#define LDAC_DEST_MUT 120

/* Statistics of the open stream for the PC (hooks.c, opcode 0xA5), NULL when none. The OTA
 * thread only copies it; a copy racing with deinit reads freed heap memory, never writes. */
const ldac_stats_t *ldac_stats_get(void) { return g_last ? ST : NULL; }
/* Stock system timer (hal_sys_timer_get at 0x0C0276E8 = -value): a peripheral read, no
 * privileged access needed (DWT/DEMCR are avoided on purpose). */
static uint32_t now(void) { return 0u - *(volatile uint32_t *)0x40002004u; }
static uint16_t sat16(uint32_t v) { return v > 0xFFFF ? 0xFFFF : (uint16_t)v; }

static void *list(void) { return g_ctx ? *(void **)(g_ctx + 0x90) : NULL; }
static int ready(void) { return g_ctx && g_last && list(); }

static void zero(void *p, uint32_t n) { uint8_t *d = p; while (n--) *d++ = 0; }

static int heap_has(uint32_t n)
{
    uint32_t total = 0, used = 0, max_used = 0;
    a2dp_audio_heap_info(&total, &used, &max_used);
    uint32_t free = total > used ? total - used : 0;
    if (g_last && free < ST->heap_free_min) ST->heap_free_min = free;
    return free >= n + HEAP_MARGIN;
}

static void last_set(void) { if (g_last) a2dp_audio_decoder_internal_lastframe_info_set(g_last); }

/* ldac_lib's allocator hooks (its syspool_get_buff/syspool_free_size reach these through the
 * payload import table, see ldac_ram.h). */
static uint32_t lib_free_size(void)
{
    if (g_pool == POOL_HEAP && g_last) {
        uint32_t total = 0, used = 0, max_used = 0;
        a2dp_audio_heap_info(&total, &used, &max_used);
        return total > used ? total - used : 0;
    }
    return stock_syspool_free_size();
}

static int lib_get_buff(uint8_t **buff, uint32_t size)
{
    size = (size + 3) & ~3u;
    if (g_pool != POOL_HEAP || !g_last)
        return stock_syspool_get_buff(buff, size);
    uint32_t before = lib_free_size();
    uint8_t *p = NULL;
    for (int i = 0; i < LIB_ALLOCS_MAX; i++) {
        if (!g_lib_allocs[i]) {
            if (heap_has(size)) {               /* never let the stock heap assert */
                p = a2dp_audio_heap_malloc(size);
                zero(p, size);
                g_lib_allocs[i] = p;
            }
            break;
        }
    }
    *buff = p;
    return (int)before;
}

/* ---- the LDAC library in RAM (ldac_lib from flash XIP was too slow, see ldac_ram.h) ---- */
extern const uint8_t ldac_payload[];
static const struct ldac_ram_exports *g_ram;   /* exports of the relocated copy, NULL if none */
static uint8_t *g_ram_block;                   /* A2DP-heap block holding the copy (as allocated) */

/* RAM (data alias) -> RAMX (instruction alias): 0x20xxxxxx -> 0x00(xxxxxx + 0x200000), as the stock
 * calls its own RAM copies. Only for blocks below 0x20200000. */
#define RAMX_OF(a)  ((uint32_t)(a) - 0x20000000u + 0x00200000u)

static uint32_t rd32(const uint8_t *p) { uint32_t v; __builtin_memcpy(&v, p, 4); return v; }
static void wr32(uint8_t *p, uint32_t v) { __builtin_memcpy(p, &v, 4); }

/* Copy the payload into the A2DP heap and relocate it. Returns 0 on success (g_ram set), else an
 * error code for the statistics (ram_err). */
static int ram_load(void)
{
    const struct ldac_ram_image *im = (const struct ldac_ram_image *)ldac_payload;
    const uint8_t *src = ldac_payload + sizeof *im;
    if (im->magic != LDAC_RAM_MAGIC || im->code_size > im->load_size || (im->load_size & 3))
        return 1;
    const uint32_t *rel = (const uint32_t *)(src + im->load_size);
    uint32_t sum = 0;
    for (uint32_t i = 0; i < im->load_size; i += 4) sum += rd32(src + i);
    for (uint32_t i = 0; i < im->nrel; i++) sum += rel[i];
    if (sum != im->crc)
        return 2;                                   /* corrupted image: never run it */
    uint32_t total = im->load_size + im->bss_size;
    if (!heap_has(total + 8 + 64))
        return 3;
    uint8_t *raw = a2dp_audio_heap_malloc(total + 8);
    /* 8-aligned like LDAC_RAM_VBASE (section alignment is relative to it); RAMX alias range */
    uint8_t *b = (uint8_t *)(((uint32_t)raw + 7) & ~7u);
    if ((uint32_t)b < 0x20000000u || (uint32_t)b + total > 0x20200000u) {
        a2dp_audio_heap_free(raw);
        return 4;
    }
    for (uint32_t i = 0; i < im->load_size; i++) b[i] = src[i];
    zero(b + im->load_size, im->bss_size);
    const uint32_t code = RAMX_OF(b), data = (uint32_t)b;
    for (uint32_t i = 0; i < im->nrel; i++) {
        uint32_t off = rel[i];
        if (off > im->load_size - 4) { a2dp_audio_heap_free(raw); return 5; }
        uint32_t o = rd32(b + off) - LDAC_RAM_VBASE;
        if (o >= total) { a2dp_audio_heap_free(raw); return 6; }
        wr32(b + off, (o < im->code_size ? code : data) + o);
    }
    __asm volatile("dsb 0xF\n\tisb 0xF" ::: "memory");   /* code written through the data bus */
    const struct ldac_ram_exports *ex = (const struct ldac_ram_exports *)b;
    if (ex->magic != LDAC_RAM_MAGIC || (uint8_t *)ex->imports < b || (uint8_t *)ex->imports >= b + total) {
        a2dp_audio_heap_free(raw);
        return 7;
    }
    ex->imports->free_size = lib_free_size;
    ex->imports->get_buff = lib_get_buff;
    g_ram_block = raw;
    g_ram = ex;
    return 0;
}

static void ram_unload(void)
{
    uint8_t *b = g_ram_block;
    g_ram = NULL;
    g_ram_block = NULL;
    if (b) a2dp_audio_heap_free(b);
}

static void lib_allocs_free(void)
{
    if (!g_last) return;
    for (int i = 0; i < LIB_ALLOCS_MAX; i++) {
        if (g_lib_allocs[i]) a2dp_audio_heap_free(g_lib_allocs[i]);
        g_lib_allocs[i] = NULL;
    }
}

/* CPU speed probe: a cached two-instruction loop, timed with the system timer. */
static uint16_t cpu_loop_ticks_n(uint32_t n)
{
    uint32_t t0 = now();
    __asm volatile("1: subs %0, %0, #1\n\tbne 1b" : "+r"(n) : : "cc");
    return sat16(now() - t0);
}

static int ldac_init(out_cfg_t *cfg, void *context)
{
    g_inits++;
    ldac_dbg.init_fs[1] = ldac_dbg.init_fs[0];
    ldac_dbg.init_fs[0] = cfg->sample_rate;
    g_pool = POOL_NONE;
    g_ctx = context;
    if (*(uint16_t *)(g_ctx + 0x16) == 0)          /* written by a2dp_audio_init just before us */
        *(uint16_t *)(g_ctx + 0x16) = LDAC_DEST_MUT;
    g_h = NULL;
    g_carry_off = g_carry_len = 0;
    g_fade = FADE_SAMPLES;
    g_last = NULL;
    g_frame_samples = cfg->sample_rate > 48000 ? 256 : 128;
    if (!heap_has(sizeof(blk_t) + 64))
        return MEMORY_ERROR;                  /* every other entry point checks ready() */
    uint32_t sys_free = stock_syspool_free_size();
    g_last = a2dp_audio_heap_malloc(sizeof(blk_t));
    zero(g_last, sizeof(blk_t));
    ST->ver = LDAC_STATS_VER;
    ST->inits = g_inits;
    ST->deinits = g_deinits;
    ST->sample_rate = cfg->sample_rate;
    ST->heap_free_min = 0xFFFFFFFFu;
    ST->syspool_free_init = sys_free;
    heap_has(0);
    ST->heap_free_init = ST->heap_free_min;
    g_last->stream_info = *cfg;
    g_last->frame_samples = g_last->list_samples = g_frame_samples;
    last_set();
    g_ram = NULL;
    g_ram_block = NULL;
    ST->ram_err = (uint16_t)ram_load();
    ST->ram_base = (uint32_t)g_ram;
    /* A2DP heap first (created per stream, freed by us in deinit). The syspool is only a fallback:
     * v4d used it first and produced loud clicks (suspected reuse of syspool memory). */
    if (heap_has(LDAC_LIB_NEED + LDAC_LIB_MARGIN + LDAC_FRAMES_RESERVE))
        g_pool = POOL_HEAP;
    else if (stock_syspool_free_size() >= LDAC_LIB_NEED + LDAC_LIB_MARGIN)
        g_pool = POOL_SYSPOOL;
    else
        g_pool = POOL_NONE;
    if (!g_ram)
        g_pool = POOL_NONE;                    /* no library: frames become silence */
    if (g_pool != POOL_NONE) {
        HANDLE_LDAC_BT h = g_ram->get_handle();
        if (h && g_ram->init_decode(h, LDACBT_CHANNEL_MODE_STEREO, (int)cfg->sample_rate, 0, 0, 0) == 0)
            g_h = h;
        else
            g_pool = POOL_NONE;
    }
    ST->pool = g_pool;
    /* LDAC decodes on the MCU and needs the top system frequency (~208 MHz; the A2DP default is
     * ~104 MHz, where decoding takes ~47 % of real time and misses DMA periods). The stock
     * rewrites the A2DP user (0xf) after init (measured: 104 MHz while playing), so use a user
     * the stock never passes (0x1e; stock uses 0x0, 0x5, 0xc..0x1b) and release it in deinit. */
    if (g_h) stock_sysfreq_req(LDAC_FREQ_USER, 8);
    ST->cpu_loop_ticks = cpu_loop_ticks_n(400000);   /* after the optional sysfreq boost */
    return NO_ERROR;
}

static int ldac_deinit(void)
{
    g_deinits++;
    if (g_last) {
        ldac_dbg.prev_frames = ST->frames;
        ldac_dbg.prev_bad = ST->bad_frames;
        ldac_dbg.prev_underflows = ST->underflows;
    }
    stock_sysfreq_req(LDAC_FREQ_USER, 0);
    if (g_h && g_ram) g_ram->free_handle(g_h);
    g_h = NULL;
    if (g_pool == POOL_HEAP) lib_allocs_free();
    g_pool = POOL_NONE;
    ram_unload();
    lastframe_t *b = g_last;
    g_last = NULL;
    if (b) a2dp_audio_heap_free(b);
    return NO_ERROR;
}

static void drop_front(void)
{
    if (!ready()) return;
    void *n = a2dp_audio_list_begin(list());
    if (n) a2dp_audio_list_remove(list(), a2dp_audio_list_node(n));
}


static void put_pcm(uint8_t *dst, const uint8_t *src, uint32_t n)
{
    const int16_t *in = (const int16_t *)src;
    int16_t *out = (int16_t *)dst;
    for (uint32_t i = 0; i < n / 2; i += 2) {
        int32_t l = in[i], r = in[i + 1];
        if (g_fade) {
            uint32_t k = FADE_SAMPLES - g_fade;
            l = l * (int32_t)k / FADE_SAMPLES;
            r = r * (int32_t)k / FADE_SAMPLES;
            g_fade--;
        }
        out[i] = (int16_t)l;
        out[i + 1] = (int16_t)r;
    }
}

static void silence(uint8_t *out, uint32_t n)
{
    zero(out, n);
    g_fade = FADE_SAMPLES;
}

static int ldac_decode_frame(uint8_t *out, uint32_t bytes)
{
    uint32_t done = 0;
    int rc = NO_ERROR;
    bytes &= ~3u;                                  /* whole stereo 16-bit samples */
    if (!ready()) {
        silence(out, bytes);
        return NO_ERROR;
    }
    uint32_t t0 = now();
    /* period in timer ticks, timer ~16 kHz (calibrated on the device: 16.0-16.1 kHz) */
    uint32_t period = (bytes / 4) * 16000u / (ST->sample_rate ? ST->sample_rate : 48000u);
    if (!ST->decode_calls) {
        ST->t_first = t0;
    } else {
        uint32_t gap = t0 - ST->t_last;
        if (sat16(gap) > ST->gap_max) ST->gap_max = sat16(gap);
        if (gap * 2 > period * 3 && ST->late_gaps < 0xFFFF) ST->late_gaps++;
    }
    ST->t_last = t0;
    ST->decode_calls++;
    ST->req_samples += bytes / 4;
    {
        float fac;
        uint32_t bits;
        __builtin_memcpy(&fac, g_ctx + 0xc, 4);
        __builtin_memcpy(&bits, g_ctx + 0xc, 4);
        float lo, hi;
        __builtin_memcpy(&lo, &ST->factor_min, 4);
        __builtin_memcpy(&hi, &ST->factor_max, 4);
        if (ST->decode_calls == 1 || fac < lo) ST->factor_min = bits;
        if (ST->decode_calls == 1 || fac > hi) ST->factor_max = bits;
        ST->factor = bits;
        ST->dest_mut = *(uint16_t *)(g_ctx + 0x16);
    }
    while (done < bytes) {
        if (g_carry_len) {                         /* finish the previous frame first */
            uint32_t n = g_carry_len < bytes - done ? g_carry_len : bytes - done;
            put_pcm(out + done, g_pcm + g_carry_off, n);
            g_carry_off = (uint16_t)(g_carry_off + n);
            g_carry_len = (uint16_t)(g_carry_len - n);
            done += n;
            continue;
        }
        void *node = a2dp_audio_list_begin(list());
        if (!node) {
            g_last->undecode_frames = 0;
            last_set();
            silence(out + done, bytes - done);
            ST->underflows++;
            rc = CACHE_UNDERFLOW;
            break;
        }
        frame_t *f = a2dp_audio_list_node(node);
        int used = 0, wrote = 0;
        uint32_t tf = now();
        /* No decoder (no memory) or a bad frame: consume the frame, output one frame of silence,
         * so the list keeps draining and the framework's bookkeeping stays consistent. */
        if (!g_h || g_ram->decode(g_h, f->buf, g_pcm, LDACBT_SMPL_FMT_S16, (int)f->len,
                                  &used, &wrote) != 0 || wrote <= 0 || wrote > FRAME_PCM_MAX || (wrote & 3)) {
            wrote = (int)(g_frame_samples * 4);
            zero(g_pcm, (uint32_t)wrote);
            g_fade = FADE_SAMPLES;
            if (!ST->bad_frames)                   /* black box: header of the first bad frame */
                for (int i = 0; i < 4; i++) ldac_dbg.bad_hdr[i] = i < (int)f->len ? f->buf[i] : 0;
            ST->bad_frames++;
        } else if ((uint32_t)wrote != g_frame_samples * 4) {
            g_frame_samples = (uint32_t)wrote / 4; /* learn the real frame size */
        }
        uint32_t dfull = now() - tf;
        ST->t_frame_sum += dfull;
        uint16_t df = sat16(dfull);
        if (df > ST->t_frame_max) ST->t_frame_max = df;
        ST->frames++;
        g_carry_off = 0;
        g_carry_len = (uint16_t)wrote;
        g_last->seq = f->seq;
        g_last->ts = f->ts;
        g_last->cur = f->cur;
        g_last->total = f->total;
        g_last->frame_samples = g_last->list_samples = g_frame_samples;
        g_last->decoded_frames++;
        g_last->undecode_frames = a2dp_audio_list_length(list()) - 1;
        last_set();
        a2dp_audio_list_remove(list(), f);         /* frees f through packet_free */
    }
    uint32_t dt = now() - t0;
    ST->t_busy += dt;
    if (sat16(dt) > ST->t_call_max) ST->t_call_max = sat16(dt);
    if (dt > period && ST->slow_calls < 0xFFFF) ST->slow_calls++;
    return rc;
}

static int ldac_preparse(uint8_t *header, uint8_t *buf, uint32_t bytes)
{
    (void)buf; (void)bytes;
    if (!ready()) return NO_ERROR;
    g_last->seq = *(uint16_t *)(header + 4);
    g_last->ts = *(uint32_t *)(header + 8);
    g_last->cur = g_last->total = 0;
    g_last->frame_samples = g_last->list_samples = g_frame_samples;
    g_last->decoded_frames = g_last->undecode_frames = 0;
    last_set();
    return NO_ERROR;
}

/* LDAC frame: 0xAA sync, then (b1 & 7) << 6 | b2 >> 2 = frame length field; +4 header bytes. */
static uint32_t frame_len(const uint8_t *p) { return (((uint32_t)(p[1] & 7) << 6) | (p[2] >> 2)) + 4; }

static int ldac_store(uint8_t *header, uint8_t *buf, uint32_t bytes)
{
    if (!ready()) return MEMORY_ERROR;
    ST->store_calls++;
    if ((ST->store_calls & 255) == 0) {        /* CPU speed while playing (BT thread, no deadline) */
        ST->cpu_loop_run = cpu_loop_ticks_n(100000);
        ST->cpu_loop_runs++;
    }
    if (bytes < 4) return NO_ERROR;
    buf++; bytes--;                                 /* media payload header (frame count) */
    /* Frame header: 0xAA, then the sampling-rate id in the top 3 bits (0 44.1k, 1 48k, ...). A
     * mismatch with the configured rate means the phone reconfigured the stream behind our back
     * (H2 gets an empty codec struct for vendor codecs): fix the device context and return
     * SYNC_ERROR, which makes the player restart (FUN_0c127274 -> FUN_0c08a4d0(0xe)) and set
     * everything up again at the right rate. */
    if (buf[0] == 0xAA) {
        uint32_t id = buf[1] >> 5;
        uint32_t rate = id == 0 ? 44100 : id == 1 ? 48000 : 0;
        ldac_dbg.frame_rate_id = (uint8_t)id;
        if (rate && rate == ST->sample_rate) {
            g_rate_fixes = 0;
        } else if (rate) {
            if (BLK->rate_fix_asked)
                return NO_ERROR;             /* restart pending: drop, do not queue undecodable frames */
            if (g_rate_fixes < RATE_FIX_MAX && ldac_set_ctx_rate(rate)) {
                g_rate_fixes++;
                ldac_dbg.rate_fixes++;
                BLK->rate_fix_asked = 1;
                return SYNC_ERROR;
            }
        }
    }
    uint32_t n = 0;
    for (uint32_t i = 0; i + 3 <= bytes && buf[i] == 0xAA; i += frame_len(buf + i)) n++;
    uint32_t len_now = a2dp_audio_list_length(list());
    ST->list_now = sat16(len_now);
    if (len_now > ST->list_max) ST->list_max = sat16(len_now);
    if (len_now + n >= LDAC_LIST_LIMIT) {
        ST->mtu_limits++;
        return MTU_LIMITER;
    }
    uint32_t cnt = 0;
    for (uint32_t i = 0; i + 3 <= bytes && buf[i] == 0xAA; cnt++) {
        uint32_t len = frame_len(buf + i);
        if (i + len > bytes) break;
        if (!heap_has(len + sizeof(frame_t) + 64)) {
            ST->mem_drops++;
            return MEMORY_ERROR;
        }
        frame_t *f = a2dp_audio_heap_malloc(sizeof(frame_t));
        f->buf = a2dp_audio_heap_malloc(len);
        for (uint32_t k = 0; k < len; k++) f->buf[k] = buf[i + k];
        f->len = len;
        f->seq = *(uint16_t *)(header + 4);
        f->ts = *(uint32_t *)(header + 8);
        f->cur = (uint16_t)cnt;
        f->total = (uint16_t)n;
        f->pad = 0;
        a2dp_audio_list_append(list(), f);
        ST->store_frames++;
        i += len;
    }
    return NO_ERROR;
}

/* Stock SBC semantics (overlay 1, 0x00201144): first drop frames until the head starts a packet
 * (cur == 0), then drop `packets` WHOLE packets; -3 if there are not that many. v7d dropped
 * `packets` frames instead: with 3-4 frames per LDAC packet the TWS slave, which discards packets to
 * line up with the master, ended up 2-3 frames (5-8 ms) late ("ligero retardo" between ears). */
static int ldac_discards_packet(uint32_t packets)
{
    if (!ready()) return NO_ERROR;
    ST->disc_packet_calls++;
    void *n;
    while ((n = a2dp_audio_list_begin(list())) != NULL && ((frame_t *)a2dp_audio_list_node(n))->cur != 0)
        drop_front();
    if (!n) return MEMORY_ERROR;
    frame_t *h = a2dp_audio_list_node(n);
    if (!h->total || packets > a2dp_audio_list_length(list()) / h->total) return MEMORY_ERROR;
    for (uint32_t i = 0; i < packets; i++) {
        drop_front();                                            /* the packet's first frame */
        while ((n = a2dp_audio_list_begin(list())) != NULL &&
               ((frame_t *)a2dp_audio_list_node(n))->cur != 0)
            drop_front();                                        /* its other frames */
    }
    return NO_ERROR;
}

static int ldac_sync_packet(headframe_t *s, uint32_t mask)
{
    if (!ready()) return SYNC_ERROR;
    ST->sync_calls++;
    uint32_t len = a2dp_audio_list_length(list());
    for (uint32_t i = 0; i < len; i++) {
        void *n = a2dp_audio_list_begin(list());
        if (!n) break;
        frame_t *f = a2dp_audio_list_node(n);
        if ((f->seq == s->seq || !(mask & 1)) && (f->cur == s->cur || !(mask & 4)) &&
            (f->total == s->total || !(mask & 8)))
            return NO_ERROR;
        a2dp_audio_list_remove(list(), f);
    }
    ST->sync_fails++;
    return SYNC_ERROR;
}

static int ldac_sync_dest_packet_mut(uint16_t mut)
{
    if (!ready()) return NO_ERROR;
    while (a2dp_audio_list_length(list()) > mut && a2dp_audio_list_begin(list())) drop_front();
    return NO_ERROR;
}

static int ldac_convert_list_to_samples(uint32_t *samples)
{
    *samples = ready() ? a2dp_audio_list_length(list()) * g_frame_samples : 0;
    return NO_ERROR;
}

static int ldac_discards_samples(uint32_t samples)
{
    if (!ready()) return NO_ERROR;
    ST->disc_samples_calls++;
    uint32_t frames = samples / g_frame_samples;
    if (frames > a2dp_audio_list_length(list())) return SYNC_ERROR;
    for (uint32_t i = 0; i < frames; i++) drop_front();
    return NO_ERROR;
}

static int ldac_headframe_info_get(headframe_t *h)
{
    void *n = ready() && a2dp_audio_list_length(list()) ? a2dp_audio_list_begin(list()) : NULL;
    if (!n) { zero(h, sizeof *h); return NO_ERROR; }
    frame_t *f = a2dp_audio_list_node(n);
    h->seq = f->seq; h->ts = f->ts; h->cur = f->cur; h->total = f->total;
    return NO_ERROR;
}

static int ldac_info_get(void *info) { (void)info; return NO_ERROR; }

static void ldac_packet_free(void *p)
{
    frame_t *f = p;
    a2dp_audio_heap_free(f->buf);
    a2dp_audio_heap_free(f);
}

static int ldac_channel_select(int sel) { (void)sel; return NO_ERROR; }

/* Stock decoder table layout (0x70): stream_info 16 B, auto_sync, 5 reserved words,
 * 14 function pointers in SDK order, 4 reserved words. stream_info is overwritten by
 * a2dp_audio_init with the real configuration. */
static const struct {
    uint32_t stream_info[4];
    uint32_t auto_synchronize_support;
    uint32_t reserved0[5];
    void *fn[14];
    uint32_t reserved1[4];
} ldac_table = {
    { 44100, 0x1002, 128, 0x3f800000 }, 1, { 0 },
    { (void *)ldac_init, (void *)ldac_deinit, (void *)ldac_decode_frame, (void *)ldac_preparse,
      (void *)ldac_store, (void *)ldac_discards_packet, (void *)ldac_sync_packet,
      (void *)ldac_sync_dest_packet_mut, (void *)ldac_convert_list_to_samples,
      (void *)ldac_discards_samples, (void *)ldac_headframe_info_get, (void *)ldac_info_get,
      (void *)ldac_packet_free, (void *)ldac_channel_select },
    { 0 },
};
_Static_assert(sizeof(ldac_table) == 0x70, "decoder table");

const void *ldac_decoder_table(void) { return &ldac_table; }
int ldac_decoder_table_ready(void) { return 1; }
