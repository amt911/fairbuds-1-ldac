#!/usr/bin/env bash
# Build an ELF of the stock image with every region at its runtime address, for llvm-objdump/Ghidra.
#   flash image          -> 0x0C020000 (FLASHX: code is linked/executed here)
#                         and 0x2C020000 (data alias: strings/const are referenced here)
#   boot copy 1  0x1788..0x90F4  -> 0x20060180
#   boot copy 2  0x90F4..0x19A74 -> 0x2006FE40   (incl. overlay manager + overlay table)
#   boot copy 3  0x19A74..0x21F34-> 0x20180000
#   .data init   0x194704..0x196ED8 -> 0x20080840 (crt0 at 0x2C020036)
#   overlay 1 (A2DP SBC decoder): text 0x317A8..0x325F0 -> exec 0x00200CC0 (RAMX of 0x20000CC0),
#                                 data 0x46388..0x46BDC -> 0x2001C29C   (tables at 0x2007B950/80/B0/E0)
#   RAM copies are also mapped at their RAMX (instruction) alias: RAM 0x20xxxxxx <-> 0x00xxxxxx+0x200000
#     (0x20060180->0x00260180, 0x2006FE40->0x0026FE40, 0x20180000->0x00380000): calls use RAMX.
# (from the reset path: 0x2C0214D4 / 0x2C021440). Usage: mkelf.sh IMAGE OUT.elf
set -euo pipefail
img="$1"; out="$2"; tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cut() { dd if="$img" of="$tmp/$1.bin" bs=1 skip=$(($2)) count=$(($3-$2)) status=none; }
cp "$img" "$tmp/flash.bin"; cp "$img" "$tmp/flashx.bin"
cut ram1 0x1788 0x90F4; cut ram2 0x90F4 0x19A74; cut ram3 0x19A74 0x21F34; cut data 0x194704 0x196ED8; cut ovlt 0x317A8 0x325F0; cut ovld 0x46388 0x46BDC; cut ram1x 0x1788 0x90F4; cut ram2x 0x90F4 0x19A74; cut ram3x 0x19A74 0x21F34
for r in flashx flash ram1 ram2 ram3 data ovlt ovld ram1x ram2x ram3x; do
  (cd "$tmp" && llvm-objcopy -I binary -O elf32-littlearm -B arm --rename-section .data=.text_$r,alloc,load,code,contents $r.bin $r.o)
done
cat > "$tmp/l.ld" <<'LD'
PHDRS { q1 PT_LOAD; q2 PT_LOAD; q3 PT_LOAD; p6 PT_LOAD; p7 PT_LOAD; p0 PT_LOAD; p1 PT_LOAD; p2 PT_LOAD; p5 PT_LOAD; p3 PT_LOAD; p4 PT_LOAD; }
SECTIONS {
  .ovl_sbc_text 0x00200CC0 : { ovlt.o(.text_ovlt) } :p6
  .ram1x 0x00260180 : { ram1x.o(.text_ram1x) } :q1
  .ram2x 0x0026FE40 : { ram2x.o(.text_ram2x) } :q2
  .ram3x 0x00380000 : { ram3x.o(.text_ram3x) } :q3
  .ovl_sbc_data 0x2001C29C : { ovld.o(.text_ovld) } :p7
  .flashx 0x0C020000 : { flashx.o(.text_flashx) } :p0
  .ram1  0x20060180 : { ram1.o(.text_ram1) } :p1
  .ram2  0x2006FE40 : { ram2.o(.text_ram2) } :p2
  .data  0x20080840 : { data.o(.text_data) } :p5
  .ram3  0x20180000 : { ram3.o(.text_ram3) } :p3
  .flash 0x2C020000 : { flash.o(.text_flash) } :p4
}
LD
(cd "$tmp" && ld.lld -m armelf -e 0x0C020014 -T l.ld flashx.o flash.o ram1.o ram2.o ram3.o data.o ovlt.o ovld.o ram1x.o ram2x.o ram3x.o -o out.elf --no-check-sections)
cp "$tmp/out.elf" "$out"; echo "wrote $out"
