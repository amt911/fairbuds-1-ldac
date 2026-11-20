#!/usr/bin/env bash
# Each sabotaged decoder build must make emu_decoder.py fail.
# Usage: work/ldac/test/sabotage_decoder.sh   (from the repo root; takes a while: one emulation per variant)
set -uo pipefail
root="$(cd "$(dirname "$0")/../../.." && pwd)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
missed=0
variant() {
  local name="$1" sedexpr="$2"
  rm -rf "$tmp/w"; mkdir -p "$tmp/w"
  cp -r "$root/work/ldac/src" "$root/work/ldac/inc" "$root/work/ldac/lib" "$root/work/ldac/link.ld" \
        "$root/work/ldac/link_ram.ld" "$root/work/ldac/stock_syms.ld" "$root/work/ldac/Makefile" "$tmp/w/"
  ln -sfn "$root/work/tools" "$tmp/tools"           # the Makefile runs ../tools/mkpayload.py
  sed -i "$sedexpr" "$tmp/w/src/ldac_decoder.c"
  if cmp -s "$tmp/w/src/ldac_decoder.c" "$root/work/ldac/src/ldac_decoder.c"; then
    echo "BROKEN  $name: sabotage did not change the source"; missed=1; return
  fi
  (cd "$tmp/w" && make addon >/dev/null 2>&1) || { echo "BROKEN  $name: build failed"; missed=1; return; }
  python3 "$root/work/tools/build_ldac_image.py" --blob "$tmp/w/build/addon.elf" --out "$tmp/img.bin" >/dev/null 2>&1
  local r
  r=$(timeout 1700 "$root/work/.venv/bin/python" -u "$root/work/ldac/test/emu_decoder.py" "$tmp/img.bin" "$tmp/w/build/addon.elf" 2>&1 | tail -1)
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
[[ $missed == 0 ]] && echo "all decoder sabotages caught" || { echo "SOME DECODER SABOTAGE MISSED"; exit 1; }
