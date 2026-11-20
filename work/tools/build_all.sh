#!/usr/bin/env bash
# Build the flashable Fairbuds images from inputs you obtain yourself (nothing from Fairphone or Sony
# is stored in this repository):
#   * the official Fairbuds Android app (APK), which ships firmware 6.5 as res/raw/fw_fairbuds_6_5.bin
#   * Sony's LDAC decoder library and header, downloaded from the public OpenPineBuds SDK (pinned
#     commit, checked by SHA-256)
# Outputs in work/patched/:
#   stock_6_5.bin               the untouched official firmware (to go back to stock)
#   sbcxq_6_5.bin               stock + SBC bitpool 53 ("SBC-XQ"), nothing else
#   ldac/fairbuds_6_5_ldac.bin  stock + SBC-XQ + LDAC
# Usage: work/tools/build_all.sh PATH/TO/Fairbuds.apk
# Needs: unzip, curl, sha256sum, python3, clang and ld.lld (LLVM), make.
set -euo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
w="$root/work"
apk="${1:?usage: work/tools/build_all.sh PATH/TO/Fairbuds.apk}"

STOCK_SHA=97e96e2b6ef953b7d27da6952515e6356e775e60e7487338ffd66e1e702c89a1
LIB_SHA=47788c136c1e82c0609abfc1fbc9e5c388b366f521b4df0ecdb119c1f610a26b
HDR_SHA=1d4aa34d9527f7150ac1937b1d035079f629a41eea586358833a78589be2b228
OPB=https://raw.githubusercontent.com/pine64/OpenPineBuds/81b9afc71ac7e5d41a10ff9229279e645bbc1175/thirdparty/audio_codec_lib/ldac

check() {  # check FILE SHA256
  if ! echo "$2  $1" | sha256sum -c --quiet - >/dev/null 2>&1; then
    echo "SHA-256 mismatch for $1 (expected $2): wrong input, refusing to continue" >&2
    exit 1
  fi
}
fetch() {  # fetch URL FILE SHA256 (kept if already present and correct)
  if [ -f "$2" ] && echo "$3  $2" | sha256sum -c --quiet - >/dev/null 2>&1; then return; fi
  curl -fsSL -o "$2" "$1"
  check "$2" "$3"
}

mkdir -p "$w/apk" "$w/ldac/lib" "$w/ldac/inc" "$w/patched/ldac"

echo "== firmware 6.5 from the app"
unzip -p "$apk" res/raw/fw_fairbuds_6_5.bin > "$w/apk/fw_fairbuds_6_5.bin" || {
  echo "this APK has no res/raw/fw_fairbuds_6_5.bin (app 5.3.0 has it)" >&2; exit 1; }
check "$w/apk/fw_fairbuds_6_5.bin" "$STOCK_SHA"
cp "$w/apk/fw_fairbuds_6_5.bin" "$w/patched/stock_6_5.bin"

echo "== LDAC library and header from OpenPineBuds"
fetch "$OPB/lib/ldac_lib.a" "$w/ldac/lib/ldac_lib.a" "$LIB_SHA"
fetch "$OPB/inc/ldacBT.h" "$w/ldac/inc/ldacBT.h" "$HDR_SHA"

echo "== SBC-XQ image"
python3 "$w/tools/besfw.py" patch "$w/patched/stock_6_5.bin" "$w/patched/sbcxq_6_5.bin" 14423B:27:35

echo "== LDAC image"
make -s -C "$w/ldac" clean
make -s -C "$w/ldac" addon >/dev/null
python3 "$w/tools/build_ldac_image.py" --blob "$w/ldac/build/addon.elf" --out "$w/patched/ldac/fairbuds_6_5_ldac.bin"

echo "== device-side check of every image (what the earbuds verify before accepting an OTA)"
for f in "$w/patched/stock_6_5.bin" "$w/patched/sbcxq_6_5.bin" "$w/patched/ldac/fairbuds_6_5_ldac.bin"; do
  python3 "$w/tools/besfw.py" check "$f" | tail -1
done
echo "done: images in work/patched/"
