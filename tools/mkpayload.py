#!/usr/bin/env python3
"""Build the LDAC RAM payload image (ldac/src/ldac_ram.h) from an ELF linked with
--emit-relocs at LDAC_RAM_VBASE.

Checks (any failure aborts the build):
  * relocation types: only R_ARM_ABS32 (relocated at run time when it points into the payload)
    and PC-relative branches whose decoded target lies inside the payload code;
  * no other word of the image points into the payload without an ABS32 relocation
    (a pointer we would not relocate);
  * layout: code, rodata+data contiguous from VBASE, sizes multiple of 4.
Usage: mkpayload.py PAYLOAD.elf OUT.img
"""
import struct
import sys

VBASE = 0x10000000
MAGIC = 0x4D41524C
R_ABS32 = 2
BRANCHES = {10: "THM_CALL", 30: "THM_JUMP24", 51: "THM_JUMP19", 102: "THM_JUMP11", 103: "THM_JUMP8",
            52: "THM_JUMP6"}
IGNORED = {0: "NONE", 40: "V4BX"}


def die(msg):
    raise SystemExit(f"mkpayload: {msg}")


class Elf:
    def __init__(self, data):
        self.d = data
        if data[:4] != b"\x7fELF" or data[4] != 1 or data[5] != 1:
            die("not an ELF32 little-endian file")
        shoff, = struct.unpack_from("<I", data, 0x20)
        shentsize, shnum, shstrndx = struct.unpack_from("<HHH", data, 0x2E)
        self.sh = [struct.unpack_from("<10I", data, shoff + i * shentsize) for i in range(shnum)]
        stro = self.sh[shstrndx][4]
        self.names = [self._str(stro, s[0]) for s in self.sh]

    def _str(self, off, i):
        e = self.d.index(b"\0", off + i)
        return self.d[off + i:e].decode()

    def section(self, name):
        for n, s in zip(self.names, self.sh):
            if n == name:
                return s
        return None

    def symbols(self):
        out = {}
        for s in self.sh:
            if s[1] == 2:  # SHT_SYMTAB
                stro = self.sh[s[6]][4]
                for i in range(s[5] // 16):
                    name, value, size, info, other, shndx = struct.unpack_from("<IIIBBH", self.d, s[4] + i * 16)
                    if name:
                        out[self._str(stro, name)] = value
        return out

    def rels(self):
        for n, s in zip(self.names, self.sh):
            if s[1] == 4:
                die(f"RELA section {n} not supported")
            if s[1] == 9:  # SHT_REL
                for i in range(s[5] // 8):
                    off, info = struct.unpack_from("<II", self.d, s[4] + i * 8)
                    yield n, off, info & 0xFF, info >> 8


def branch_target(img, off):
    """Decoded target (VMA) of the Thumb branch at image offset off."""
    h1, = struct.unpack_from("<H", img, off)
    pc = VBASE + off + 4
    if h1 >> 11 == 0b11100:                                   # B T2
        imm = (h1 & 0x7FF) << 1
        return pc + (imm - (1 << 12) if imm & (1 << 11) else imm)
    if h1 >> 12 == 0b1101:                                    # B<c> T1
        imm = (h1 & 0xFF) << 1
        return pc + (imm - (1 << 9) if imm & (1 << 8) else imm)
    if h1 & 0xF500 == 0xB100:                                 # CBZ/CBNZ
        return pc + ((((h1 >> 9) & 1) << 6) | (((h1 >> 3) & 0x1F) << 1))
    h2, = struct.unpack_from("<H", img, off + 2)
    if h1 >> 11 != 0b11110:
        die(f"unknown branch encoding at +{off:#x}: {h1:04x} {h2:04x}")
    s = (h1 >> 10) & 1
    j1, j2 = (h2 >> 13) & 1, (h2 >> 11) & 1
    if h2 & 0xD000 in (0xD000, 0x9000):                       # BL T1 / B.W T4
        i1, i2 = 1 - (j1 ^ s), 1 - (j2 ^ s)
        imm = (s << 24) | (i1 << 23) | (i2 << 22) | ((h1 & 0x3FF) << 12) | ((h2 & 0x7FF) << 1)
        return pc + (imm - (1 << 25) if s else imm)
    if h2 & 0xD000 == 0x8000:                                 # B<c>.W T3
        imm = (s << 20) | (j2 << 19) | (j1 << 18) | ((h1 & 0x3F) << 12) | ((h2 & 0x7FF) << 1)
        return pc + (imm - (1 << 21) if s else imm)
    die(f"unknown 32-bit branch at +{off:#x}: {h1:04x} {h2:04x}")


def build(elf_path):
    e = Elf(open(elf_path, "rb").read())
    sym = e.symbols()
    text, data, bss = e.section(".text"), e.section(".data"), e.section(".bss")
    if not text or text[3] != VBASE:
        die(f".text must start at {VBASE:#x}")
    code_end, load_end, end = sym["__ram_code_end"], sym["__ram_load_end"], sym["__ram_end"]
    code_size, load_size, total = code_end - VBASE, load_end - VBASE, end - VBASE
    if code_size % 4 or load_size % 4 or total % 4:
        die("sizes must be multiples of 4")
    img = bytearray(load_size)
    img[:text[5]] = e.d[text[4]:text[4] + text[5]]
    if data and data[5]:
        if data[3] != VBASE + text[5]:
            die(".data must follow .text")
        img[data[3] - VBASE:data[3] - VBASE + data[5]] = e.d[data[4]:data[4] + data[5]]
    bss_size = total - load_size
    if bss and bss[5] and bss[3] != load_end:
        die(".bss must follow .data")
    if ldac_exports_off(sym) != 0:
        die("ldac_ram_exports must be the first object")

    rel = set()
    counts = {}
    for sec, off, typ, _ in e.rels():
        if sec in (".rel.ARM.exidx",) or off < VBASE or off >= VBASE + total:
            continue
        o = off - VBASE
        counts[typ] = counts.get(typ, 0) + 1
        if typ == R_ABS32:
            if o >= load_size:
                die(f"ABS32 in .bss at +{o:#x}")
            v, = struct.unpack_from("<I", img, o)
            if VBASE <= v < VBASE + total:
                rel.add(o)
        elif typ in BRANCHES:
            if o >= code_size:
                die(f"branch relocation outside code at +{o:#x}")
            t = branch_target(img, o)
            if not VBASE <= t < code_end:
                die(f"{BRANCHES[typ]} at +{o:#x} leaves the payload ({t:#x}): needs a thunk")
        elif typ not in IGNORED:
            die(f"unsupported relocation type {typ} at +{o:#x} in {sec}")
    # every word pointing into the payload must be relocated (thunk literals point outside)
    stray = [o for o in range(0, load_size - 3, 2)
             if VBASE <= struct.unpack_from("<I", img, o)[0] < VBASE + total and o not in rel
             and (o % 4 == 0 or o < code_size)]
    stray = [o for o in stray if o % 4 == 0]
    if stray:
        die(f"{len(stray)} unrelocated words point into the payload, first at "
            f"{', '.join(hex(o) for o in stray[:5])}")
    rel = sorted(rel)
    words = struct.unpack(f"<{load_size // 4}I", bytes(img)) + tuple(rel)
    crc = sum(words) & 0xFFFFFFFF
    hdr = struct.pack("<6I", MAGIC, code_size, load_size, bss_size, len(rel), crc)
    out = hdr + bytes(img) + struct.pack(f"<{len(rel)}I", *rel)
    info = (f"payload: code {code_size} B, rodata+data {load_size - code_size} B, bss {bss_size} B, "
            f"{len(rel)} relocations; reloc types {dict(sorted(counts.items()))}")
    return out, info


def ldac_exports_off(sym):
    return sym["ldac_ram_exports"] - VBASE


if __name__ == "__main__":
    out, info = build(sys.argv[1])
    open(sys.argv[2], "wb").write(out)
    print(info, f"-> {sys.argv[2]} ({len(out)} B)")
