#!/usr/bin/env python3
"""Build a Fairbuds 1 OTA image = stock 6.5 + LDAC add-on blob (+ the four hook patches).

Layout (LDAC_PORT.md):
  [stock 6.5, byte-identical except the patches] [0xFF pad to 16] [blob: .text/.rodata/.data-LMA]
  [copy of the stock trailer 0x196ED8..end, CRC re-computed]
The stock header/build_info/.data stay where they were; the device's sanity check finds the
CRC keyword in the trailer copy (last 0x200 bytes), and the last 4 bytes (start location the app
derives) are unchanged.

Safety checks (the build fails if any is violated):
  * stock SHA-256 matches; original bytes at every patch site match;
  * the BL encoder reproduces the original BLs at H1 and H3 before writing new ones;
  * blob base == 0x0C020000 + aligned stock size (the address it was linked for);
  * only the expected byte ranges differ from the stock;
  * size <= 0x1E0000 (device limit, 0x2C0AB148); besfw.device_check passes.

Usage:
  build_ldac_image.py --blob work/ldac/build/X.elf --out OUT.bin [--no-hooks] [--no-sbcxq]
"""
import argparse
import hashlib
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import besfw  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
STOCK = os.path.join(ROOT, "apk/fw_fairbuds_6_5.bin")
STOCK_SHA = "97e96e2b6ef953b7d27da6952515e6356e775e60e7487338ffd66e1e702c89a1"
CODE = 0x0C020000          # FLASHX alias the image executes at
TRAILER = 0x196ED8         # build_info_start (header +0xC = 0x2C1B6ED8)
MAX_IMAGE = 0x1E0000
SBCXQ = (0x14423B, 0x27, 0x35)

# (name, address, original bytes, kind, target symbol, original BL target or None)
HOOKS = [
    ("H1 boot / SEP register", 0x0C0BF8FA, "02f0adff", "bl", "ldac_boot_hook", 0x0C0C2858),
    ("H2 stream config 0xd", 0x0C0C027A, "b8f1000f", "bl", "ldac_cfg_hook", None),
    ("H3 decoder table copy", 0x0C127876, "13f08ffc", "bl", "ldac_dec_copy_hook", 0x0C13B198),
    ("H4 config init (ev 3)", 0x0C0C00AC, "022b40f03381", "bl+nop", "ldac_cfginit_hook", None),
    ("H5 stream open (ev 2/1f)", 0x0C0BFCA8, "2b78022b", "bl", "ldac_open_hook", None),
    ("H6 OTA debug opcode 0xA5", 0x0C0AD072, "803b192b", "bl", "ldac_dbg_hook", None),
]


def bl(pc, target):
    """Thumb-2 BL (T1) at pc to target."""
    off = target - (pc + 4)
    if off % 2 or not -(1 << 24) <= off < (1 << 24):
        raise ValueError(f"BL out of range/odd: {pc:#x} -> {target:#x}")
    s = (off >> 24) & 1
    i1 = (off >> 23) & 1
    i2 = (off >> 22) & 1
    j1 = (~i1 ^ s) & 1
    j2 = (~i2 ^ s) & 1
    hw1 = 0xF000 | (s << 10) | ((off >> 12) & 0x3FF)
    hw2 = 0xD000 | (j1 << 13) | (j2 << 11) | ((off >> 1) & 0x7FF)
    return hw1.to_bytes(2, "little") + hw2.to_bytes(2, "little")


def symbols(elf):
    out = subprocess.run(["llvm-nm", elf], capture_output=True, text=True, check=True).stdout
    syms = {}
    for line in out.splitlines():
        p = line.split()
        if len(p) == 3:
            syms[p[2]] = int(p[0], 16)
    return syms


def blob_bytes(elf):
    with tempfile.TemporaryDirectory() as t:
        out = os.path.join(t, "blob.bin")
        subprocess.run(["llvm-objcopy", "-O", "binary", "--only-section=.text",
                        "--only-section=.data", elf, out], check=True)
        return open(out, "rb").read()


def lowest_text(elf):
    out = subprocess.run(["llvm-readelf", "-S", elf], capture_output=True, text=True, check=True).stdout
    for line in out.splitlines():
        p = line.replace("[", " ").replace("]", " ").split()
        if len(p) > 4 and p[1] == ".text":
            return int(p[3], 16)
    raise SystemExit("no .text in blob ELF")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--blob", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-hooks", action="store_true", help="append the blob only (stage 3a)")
    ap.add_argument("--no-sbcxq", action="store_true")
    a = ap.parse_args()

    stock = open(STOCK, "rb").read()
    if hashlib.sha256(stock).hexdigest() != STOCK_SHA:
        raise SystemExit("stock image SHA-256 mismatch")
    img = bytearray(stock)
    allowed = []  # (start, end) file ranges allowed to differ from stock

    if not a.no_sbcxq:
        off, old, new = SBCXQ
        assert img[off] == old, "SBC-XQ site mismatch"
        img[off] = new
        allowed.append((off, off + 1))

    base_off = (len(stock) + 15) & ~15
    base = CODE + base_off
    if lowest_text(a.blob) != base:
        raise SystemExit(f"blob linked at {lowest_text(a.blob):#x}, expected {base:#x} "
                         f"(make BLOB_BASE={base:#x})")
    blob = blob_bytes(a.blob)
    syms = symbols(a.blob)

    if not a.no_hooks:
        for name, addr, orig_hex, kind, sym, orig_target in HOOKS:
            off = addr - CODE
            orig = bytes.fromhex(orig_hex)
            if bytes(img[off:off + len(orig)]) != orig:
                raise SystemExit(f"{name}: original bytes differ at {addr:#x}")
            if orig_target is not None and bl(addr, orig_target) != orig:
                raise SystemExit(f"{name}: BL encoder self-check failed")
            if sym not in syms:
                raise SystemExit(f"{name}: symbol {sym} missing from blob")
            new = bl(addr, syms[sym] & ~1)
            if kind == "bl+nop":
                new += bytes.fromhex("00bf")
            assert len(new) == len(orig)
            img[off:off + len(new)] = new
            allowed.append((off, off + len(new)))
            print(f"  {name:24} {addr:#010x}: {orig.hex()} -> {new.hex()}  (bl {syms[sym]:#x} {sym})")

    img += b"\xff" * (base_off - len(img)) + blob
    img += b"\xff" * (-len(img) % 4)
    img += stock[TRAILER:]
    allowed.append((len(stock), len(img)))

    img = bytearray(besfw.fix_trailer(bytes(img)))
    # every difference from the stock must be inside an allowed range
    for i in range(len(stock)):
        if img[i] != stock[i] and not any(s <= i < e for s, e in allowed):
            raise SystemExit(f"unexpected difference at file offset {i:#x}")
    if len(img) > MAX_IMAGE:
        raise SystemExit(f"image too large: {len(img):#x} > {MAX_IMAGE:#x}")
    ok, msgs = besfw.device_check(bytes(img))
    for m in msgs:
        print("  ", m)
    if not ok:
        raise SystemExit("device check failed")
    open(a.out, "wb").write(img)
    print(f"wrote {a.out}: {len(img)} B (stock {len(stock)}, blob {len(blob)} B at {base:#x}), "
          f"crc32(file)=0x{besfw.crc32(bytes(img)):08X}, hooks={'no' if a.no_hooks else 'yes'}")
    print(f"sha256 {hashlib.sha256(img).hexdigest()}")


if __name__ == "__main__":
    main()
