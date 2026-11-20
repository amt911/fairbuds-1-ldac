#!/usr/bin/env bash
# Each sabotaged decoder build must make emu_decoder.py fail.
# Usage: ldac/test/sabotage_decoder.sh   (from the repo root; takes a while: one emulation per variant)
set -uo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
missed=0
variant() {   # variant NAME SEDEXPR [SOURCE]  (SOURCE: file in src/, default ldac_decoder.c;
              #  hooks.c variants are checked with emu_hooks.py, the others with emu_decoder.py)
  local name="$1" sedexpr="$2" srcf="${3:-ldac_decoder.c}"
  rm -rf "$tmp/w"; mkdir -p "$tmp/w"
  cp -r "$root/ldac/src" "$root/ldac/inc" "$root/ldac/lib" "$root/ldac/link.ld" \
        "$root/ldac/link_ram.ld" "$root/ldac/stock_syms.ld" "$root/ldac/Makefile" "$tmp/w/"
  ln -sfn "$root/tools" "$tmp/tools"           # the Makefile runs ../tools/mkpayload.py
  sed -i "$sedexpr" "$tmp/w/src/$srcf"
  if cmp -s "$tmp/w/src/$srcf" "$root/ldac/src/$srcf"; then
    echo "BROKEN  $name: sabotage did not change the source"; missed=1; return
  fi
  (cd "$tmp/w" && make addon >/dev/null 2>&1) || { echo "BROKEN  $name: build failed"; missed=1; return; }
  python3 "$root/tools/build_ldac_image.py" --blob "$tmp/w/build/addon.elf" --out "$tmp/img.bin" >/dev/null 2>&1
  local r
  local t=emu_decoder.py; [[ "$srcf" == hooks.c ]] && t=emu_hooks.py
  r=$(timeout 1700 "$root/.venv/bin/python" -u "$root/ldac/test/$t" "$tmp/img.bin" "$tmp/w/build/addon.elf" 2>&1 | tail -1)
  if [[ "$r" == "ALL PASS" ]]; then echo "MISSED  $name"; missed=1; else echo "caught  $name: $r"; fi
}
variant "frame length off by one" 's/(p\[2\] >> 2)) + 4; }/(p[2] >> 2)) + 3; }/'
variant "carry buffer dropped"    's/            g_carry_off = (uint16_t)(g_carry_off + n);/            g_carry_len = n; g_carry_off = (uint16_t)(g_carry_off + n);/'
variant "underflow not reported"  's/            rc = CACHE_UNDERFLOW;/            rc = NO_ERROR;/'
variant "stray write to stock RAM" 's/    ST->store_calls++;/    ST->store_calls++; *(volatile uint32_t *)0x20090000u = 1;/'
variant "decoded frames not counted" 's/        ST->frames++;/        ;/'
variant "list limit back to 200 frames" 's/^#define LDAC_LIST_LIMIT      400$/#define LDAC_LIST_LIMIT      200/'
variant "payload relocation skipped"  's/        wr32(b + off, (o < im->code_size ? code : data) + o);/        (void)code; (void)data;/'
variant "payload relocation off by 4"  's/        wr32(b + off, (o < im->code_size ? code : data) + o);/        wr32(b + off, (o < im->code_size ? code : data) + o + 4);/'
variant "payload checksum ignored"     's/    if (sum != im->crc)$/    if (sum != im->crc \&\& 0)/'
variant "payload block not 8-aligned"  's/    uint8_t \*b = (uint8_t \*)(((uint32_t)raw + 7) \& ~7u);/    uint8_t *b = raw;/'
variant "sysfreq not released"         's/^    stock_sysfreq_req(LDAC_FREQ_USER, 0);$/    ;/'
variant "left and right swapped"        's/        out\[i\] = (int16_t)l;/        out[i] = (int16_t)r; r = l;/'
variant "discards_packet drops frames" 's/^        drop_front();                                            \/\* the packet.s first frame \*\/$/        drop_front(); continue;/'
variant "dest_packet_mut left at 0"     's/^        \*(uint16_t \*)(g_ctx + 0x16) = LDAC_DEST_MUT;$/        ;/'
variant "black box init rate not recorded" 's/^    ldac_dbg.init_fs\[0\] = cfg->sample_rate;$/    ;/'
variant "rate mismatch ignored"         's/^        } else if (rate) {$/        } else if (rate \&\& 0) {/'
variant "rate restarts not capped"      's/^            if (g_rate_fixes < RATE_FIX_MAX \&\& ldac_set_ctx_rate(rate)) {$/            if (ldac_set_ctx_rate(rate)) {/'
variant "empty vendor codec struct ignored" 's/^    if (codec\[0\] == CODEC_VENDOR \&\& codec\[1\] == 0) {$/    if (codec[0] == CODEC_VENDOR \&\& codec[1] == 0xEE) {/' hooks.c
variant "H4 does not set the rate hint"   's/^    ldac_fs_hint = out\[6\];.*$/    ;/' hooks.c
[[ $missed == 0 ]] && echo "all decoder sabotages caught" || { echo "SOME DECODER SABOTAGE MISSED"; exit 1; }
