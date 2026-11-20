/* Stock 6.5 functions/data used by the add-on. Thumb functions have bit 0 set.
 * Each entry: address + evidence. Keep unverified ones out. */
__aeabi_f2d  = 0x00272315; /* RAMX (exec alias) of RAM copy 2, as the stock calls it; bytes identical to libgcc __aeabi_f2d (SDK elf), file 0xB5C8 */
__aeabi_d2iz = 0x002728F9; /* RAMX (exec alias) of RAM copy 2, as the stock calls it; bytes identical to libgcc __aeabi_d2iz (SDK elf), file 0xBBAC */
stock_cur_a2dp_dev       = 0x0C0C3D45; /* FUN_0c0c3d44: returns current A2DP device id (decompiled) */
stock_dev_ctx            = 0x0C0BCA01; /* FUN_0c0bca00: dev*0x130+base, NULL if dev>=2 (decompiled) */
stock_aac_setup          = 0x0C0C2859; /* FUN_0c0c2858: AAC SEP setup, called from 0x0C0BF8FA (H1 site) */
stock_btif_a2dp_register = 0x0C103445; /* FUN_0c103444: called by SBC/AAC setup with (stream,1,desc,0,id,cb) */
stock_a2dp_callback      = 0x0C0BFBC8; /* FUN_0c0bfbc8: SEP callback; literal 0x0C0BFBC9 in SBC/AAC setup */
/* A2DP decoder framework (see LDAC_PORT.md "Funciones del framework identificadas") */
a2dp_audio_heap_malloc   = 0x0027B341; /* RAMX; string ref; asserts when empty -> check heap_info first */
a2dp_audio_heap_free     = 0x0027B2E5; /* RAMX; string ref */
a2dp_audio_heap_info     = 0x0C126B15; /* sums total/used/max_used over the A2DP heaps (decompiled) */
a2dp_audio_list_append   = 0x0C126CBD; /* SBC init list checker: append 250 frames */
a2dp_audio_list_remove   = 0x0C126C5D; /* SBC list checker / sync / discards: remove(list, data) */
a2dp_audio_list_length   = 0x0C126BC5; /* list checker final trace: length after clearing */
a2dp_audio_list_begin    = 0x0C126B99; /* list checker loop condition (node) */
a2dp_audio_list_node     = 0x0C126C31; /* node -> data, then remove */
a2dp_audio_decoder_internal_lastframe_info_set = 0x0C127D69; /* copies the 0x34 struct to a global */
stock_syspool_get_buff   = 0x00271FB9; /* RAMX; trace "syspool_get_buff", asserts on shortage */
stock_syspool_free_size  = 0x00271FA5; /* RAMX; total - used (same vars 0x2009EFC0/C4 as get_buff) */
stock_sysfreq_req        = 0x0C123939; /* FUN_0c123938(user, freq 0..8): a2dp_audio_init calls (0xf, 6) */
