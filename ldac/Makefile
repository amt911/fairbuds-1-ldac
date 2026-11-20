# LDAC add-on for Fairbuds 1 stock 6.5. Toolchain: clang + ld.lld (no arm-none-eabi-gcc needed).
CC      := clang
LD      := ld.lld
CFLAGS  := --target=thumbv8m.main-none-eabi -mcpu=cortex-m33 -mfloat-abi=hard -mfpu=fpv5-sp-d16 \
           -Os -ffunction-sections -fdata-sections -fno-builtin -ffreestanding -fno-common \
           -Iinc -Isrc -Wall -Wextra
# 0x0C020000 + original image size (0x197084) aligned up to 16
BLOB_BASE ?= 0x0C1B7090
# Entry points reached only from the patched stock code.
HOOKS   := ldac_boot_hook ldac_cfg_hook ldac_cfginit_hook ldac_dec_copy_hook ldac_open_hook ldac_dbg_hook
LDFLAGS := -m armelf -T link.ld --defsym BLOB_BASE=$(BLOB_BASE) --gc-sections $(addprefix -u ,$(HOOKS))

build/%.o: src/%.c src/*.h | build
	$(CC) $(CFLAGS) -c $< -o $@
# RAM payload objects: literal pools only (no movw/movt), so ABS32 words are the only
# position-dependent part (tools/mkpayload.py checks it).
RAMFLAGS := $(CFLAGS) -mno-movt
build/ram/%.o: src/%.c src/*.h | build
	mkdir -p build/ram
	$(CC) $(RAMFLAGS) -c $< -o $@
build/%.o: src/%.S | build
	$(CC) $(CFLAGS) -c $< -o $@
build:
	mkdir -p build

build/probe.elf: build/link_probe.o build/rt_helpers.o lib/ldac_lib.a link.ld stock_syms.ld
	$(LD) $(LDFLAGS) -e link_probe build/link_probe.o build/rt_helpers.o lib/ldac_lib.a -o $@

# Hooks + placeholder decoder (no LDAC lib): for emulation of the four patches.
HOOK_OBJS := build/hooks.o build/hooks_asm.o build/rt_helpers.o
build/addon_stub.elf: $(HOOK_OBJS) build/ldac_decoder_stub.o link.ld stock_syms.ld
	$(LD) $(LDFLAGS) -e ldac_boot_hook $(HOOK_OBJS) build/ldac_decoder_stub.o -o $@
stub: build/addon_stub.elf
	llvm-size -A $< | grep -E "^\.(text|data|bss)"
	llvm-nm -n $< | grep -E " [Tt] (ldac_|__gap)|__gap"

# LDAC library payload, executed from a relocated copy in RAM (src/ldac_ram.h).
build/ldac_ram.elf: build/ram/ram_glue.o build/ram/rt_helpers.o lib/ldac_lib.a link_ram.ld stock_syms.ld
	$(LD) -m armelf -T link_ram.ld --emit-relocs --gc-sections -u ldac_ram_exports -e ldac_ram_exports \
	    build/ram/ram_glue.o build/ram/rt_helpers.o lib/ldac_lib.a -o $@
build/ldac_ram.img: build/ldac_ram.elf ../tools/mkpayload.py
	python3 ../tools/mkpayload.py $< $@
build/ldac_payload.o: build/ldac_ram.img

# Full add-on: hooks + LDAC decoder (flash) + the payload image.
ADDON_OBJS := $(HOOK_OBJS) build/ldac_decoder.o build/ldac_payload.o
build/addon.elf: $(ADDON_OBJS) link.ld stock_syms.ld
	$(LD) $(LDFLAGS) -e ldac_boot_hook $(ADDON_OBJS) -o $@
addon: build/addon.elf
	llvm-size -A $< | grep -E "^\.(text|data|bss)"
	llvm-nm -n $< | grep -E "__gap_(bss|data)_(start|end)"

probe: build/probe.elf
	llvm-size -A $< | grep -E "^\.(text|data|bss)"

clean:
	rm -rf build
.PHONY: probe stub addon clean
