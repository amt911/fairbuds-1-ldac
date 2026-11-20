#!/usr/bin/env python3
"""Emulate the four LDAC hooks inside a patched image (Unicorn, Cortex-M33 Thumb).

Each test starts at the real patch site in the patched stock code and runs until control comes back
to stock code, then checks: where execution resumes, registers preserved, flags, every memory write
(must hit only allowed ranges) and the arguments passed to stock functions (stubbed).

Usage: work/.venv/bin/python work/ldac/test/emu_hooks.py IMAGE.bin BLOB.elf
"""
import struct
import subprocess
import sys

from unicorn import Uc, UcError, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.arm_const import *

CODE, DATA = 0x0C020000, 0x2C020000
RAM, RAM_SIZE = 0x20000000, 0x200000
GAP = (0x200BC734, 0x200BC800)
STACK_TOP = 0x20170000
CTX_LIT = 0x0C0BCA2C          # literal: base of the per-device A2DP contexts
SITES = {"H1": 0x0C0BF8FA, "H2": 0x0C0C027A, "H3": 0x0C127876, "H4": 0x0C0C00AC, "H5": 0x0C0BFCA8,
         "H6": 0x0C0AD072}
STUB_AAC, STUB_REG, STUB_CURDEV, STUB_TRACE = 0x0C0C2858, 0x0C103444, 0x0C0C3D44, 0x0C13B4B8
REGS = [UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5,
        UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11,
        UC_ARM_REG_R12]

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
        print("   FAIL", msg)


def syms(elf):
    out = subprocess.run(["llvm-nm", elf], capture_output=True, text=True, check=True).stdout
    return {p[2]: int(p[0], 16) for p in (l.split() for l in out.splitlines()) if len(p) == 3}


class Emu:
    def __init__(self, img):
        self.img = img
        u = self.u = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        size = (len(img) + 0xFFFF) & ~0xFFFF
        for base in (CODE, DATA):
            u.mem_map(base, size)
            u.mem_write(base, img)
        u.mem_map(RAM, RAM_SIZE)
        for fs, fe, vs in [(0x1788, 0x90F4, 0x20060180), (0x90F4, 0x19A74, 0x2006FE40),
                           (0x194704, 0x196ED8, 0x20080840)]:
            u.mem_write(vs, img[fs:fe])
        self.ctx_base = struct.unpack_from("<I", img, CTX_LIT - CODE)[0]
        self.calls = []
        self.writes = []
        self.stub_ret = {STUB_CURDEV: 0}
        self.extra = {}           # addr -> callback(u): extra stubs (return to lr afterwards)
        u.hook_add(UC_HOOK_CODE, self._code)
        u.hook_add(UC_HOOK_MEM_WRITE, self._write)

    def _code(self, u, addr, size, _):
        if addr in (STUB_AAC, STUB_REG, STUB_CURDEV, STUB_TRACE):
            r = [u.reg_read(x) for x in REGS[:4]]
            sp = u.reg_read(UC_ARM_REG_SP)
            stack = struct.unpack("<2I", u.mem_read(sp, 8))
            self.calls.append((addr, r, stack))
            u.reg_write(UC_ARM_REG_R0, self.stub_ret.get(addr, 0))
            u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR) | 1)
        if addr in self.extra:
            self.extra[addr](u)
            u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR) | 1)
        if addr in self.stop_at:
            u.emu_stop()

    def _write(self, u, access, addr, size, value, _):
        self.writes.append((addr, size))

    def run(self, start, stop_at, regs, max_insn=2000):
        u = self.u
        self.stop_at = set(stop_at)
        self.calls, self.writes = [], []
        for r, v in zip(REGS, regs):
            u.reg_write(r, v)
        u.reg_write(UC_ARM_REG_SP, STACK_TOP)
        u.reg_write(UC_ARM_REG_LR, 0xDEADBEEF)
        try:
            u.emu_start(start | 1, 0xFFFFFFFF, count=max_insn)
        except UcError as e:
            return None, f"emulation error {e} at {u.reg_read(UC_ARM_REG_PC):#x}"
        return u.reg_read(UC_ARM_REG_PC), None

    def rd(self, a, n):
        return bytes(self.u.mem_read(a, n))

    def wr(self, a, b):
        self.u.mem_write(a, b)

    def regs(self):
        return [self.u.reg_read(r) for r in REGS]

    def zflag(self):
        return bool(self.u.reg_read(UC_ARM_REG_XPSR) & (1 << 30))

    def writes_outside(self, allowed):
        bad = []
        for a, s in self.writes:
            if STACK_TOP - 0x400 <= a < STACK_TOP:
                continue
            if not any(lo <= a and a + s <= hi for lo, hi in allowed):
                bad.append(hex(a))
        return bad


SENT = [0x11110000 + i for i in range(13)]


def test_h1(e, S):
    print("H1 boot hook")
    for dev in (0, 1):
        ctx = e.ctx_base + dev * 0x130
        e.wr(ctx + 0x34, struct.pack("<I", 0x20100000 + dev * 0x100))
        e.wr(GAP[0], b"\xa5" * (GAP[1] - GAP[0]))       # uninitialised RAM
        regs = list(SENT)
        regs[0] = dev
        pc, err = e.run(SITES["H1"], [SITES["H1"] + 4], regs)
        check(err is None and pc == SITES["H1"] + 4, f"H1 dev{dev}: returns after the site ({err}, {pc})")
        aac = [c for c in e.calls if c[0] == STUB_AAC]
        reg = [c for c in e.calls if c[0] == STUB_REG]
        check(len(aac) == 1 and aac[0][1][0] == dev, f"H1 dev{dev}: original AAC setup called once with r0=dev")
        check(len(reg) == 1, f"H1 dev{dev}: btif_a2dp_register called once")
        if reg:
            r, stack = reg[0][1], reg[0][2]
            check(r[0] == 0x20100000 + dev * 0x100 and r[1] == 1 and r[3] == 0,
                  f"H1 dev{dev}: register(stream, 1, desc, 0) args {[hex(x) for x in r]}")
            check(stack == (1, 0x0C0BFBC9), f"H1 dev{dev}: priority 1, cb 0x0C0BFBC9 on stack: {stack}")
            check(GAP[0] <= r[2] < GAP[1], f"H1 dev{dev}: descriptor in the RAM gap ({r[2]:#x})")
            d = e.rd(r[2], 12)
            caps = struct.unpack_from("<I", d, 2)[0]
            check(d[0] == 0xFF and d[1] == 8 and d[10] == 1, f"H1 dev{dev}: descriptor {d.hex()}")
            check(caps >> 24 == 0x2C and e.rd(caps, 8) == bytes.fromhex("2d010000aa003001"),
                  f"H1 dev{dev}: caps via data alias {caps:#x}")
        check(e.regs()[4:12] == SENT[4:12], f"H1 dev{dev}: r4-r11 preserved")
        check(e.u.reg_read(UC_ARM_REG_SP) == STACK_TOP, f"H1 dev{dev}: sp restored")
        bad = e.writes_outside([GAP])
        check(not bad, f"H1 dev{dev}: writes outside gap/stack: {bad[:5]}")
        if dev == 0:   # ram_init ran: .data copied from flash, .bss zeroed (desc is in .bss)
            ds, de, dl = S["__gap_data_start"], S["__gap_data_end"], S["__gap_data_load"]
            check(e.rd(ds, de - ds) == e.rd(dl, de - ds), "H1 dev0: .data copied into the gap")
    # device 2 -> stock_dev_ctx returns NULL: no registration, no crash
    regs = list(SENT)
    regs[0] = 2
    pc, err = e.run(SITES["H1"], [SITES["H1"] + 4], regs)
    check(err is None and pc == SITES["H1"] + 4, f"H1 dev2: returns ({err})")
    check(not [c for c in e.calls if c[0] == STUB_REG], "H1 dev2: no registration")
    # stream NULL
    e.wr(e.ctx_base + 0x34, b"\0\0\0\0")
    regs = list(SENT)
    regs[0] = 0
    pc, err = e.run(SITES["H1"], [SITES["H1"] + 4], regs)
    check(err is None and not [c for c in e.calls if c[0] == STUB_REG], "H1 stream NULL: no registration")


def codec_struct(e, at, ctype, elems, buf_at):
    e.wr(buf_at, elems)
    e.wr(at, bytes([ctype, len(elems)]) + struct.pack("<I", buf_at) + b"\0" * 6)


def test_h2(e, S):
    print("H2 stream config hook")
    ctx = e.ctx_base
    cs, el = 0x20101000, 0x20101100
    cases = [
        ("LDAC 48k", 0xFF, bytes.fromhex("2d010000aa001001"), 0x0C0C0318, (0xFF, 0x10, 0x10, 2)),
        ("LDAC 44.1k", 0xFF, bytes.fromhex("2d010000aa002001"), 0x0C0C0318, (0xFF, 0x20, 0x10, 2)),
        ("LDAC 96k (not advertised)", 0xFF, bytes.fromhex("2d010000aa000401"), 0x0C0C0318, None),
        ("aptX vendor", 0xFF, bytes.fromhex("4f0000000100ff"), 0x0C0C0318, None),
        ("SBC", 0x00, bytes.fromhex("3fff0235"), 0x0C0C0280, None),
        ("codec 4", 0x04, bytes.fromhex("01020304"), 0x0C0C0318, None),
    ]
    flag = S.get("ldac_cfg_flag")
    for name, ctype, elems, want_pc, want in cases:
        e.wr(ctx + 0x3f, b"\x77" * 12)
        if flag:
            e.wr(flag, b"\x01")
        codec_struct(e, cs, ctype, elems, el)
        regs = list(SENT)
        regs[3], regs[4], regs[8] = cs, ctx, ctype
        pc, err = e.run(SITES["H2"], [0x0C0C0318, 0x0C0C0280], regs)
        check(err is None and pc == want_pc, f"H2 {name}: resumes at {want_pc:#x} (got {pc}, {err})")
        r = e.regs()
        check(r == regs, f"H2 {name}: r0-r12 preserved")
        f = e.rd(ctx + 0x3f, 12)
        if flag and ctype == 0:
            check(e.rd(flag, 1) == b"\x00", f"H2 {name}: SBC clears ldac_cfg_flag")
        if want:
            got = (f[0x4a - 0x3f], f[0], f[1], f[3])
            check(got == want, f"H2 {name}: ctx +4a/+3f/+40/+42 = {got} want {want}")
            if flag:
                check(e.rd(flag, 1) == b"\x01", f"H2 {name}: LDAC sets ldac_cfg_flag")
            allowed = [(ctx + 0x3f, ctx + 0x4b), GAP]
        else:
            check(f == b"\x77" * 12, f"H2 {name}: ctx untouched")
            allowed = [GAP]           # ldac_cfg_flag
        bad = e.writes_outside(allowed)
        check(not bad, f"H2 {name}: unexpected writes {bad[:5]}")


def test_h4(e):
    print("H4 config-init hook")
    rem, rel, cfg, buf = 0x20102000, 0x20102100, 0x20102200, 0x20102300
    cases = [
        ("AAC", 2, None, 0x0C0C00B2, None),
        ("LDAC remote 44.1/48/88/96 all modes", 0xFF, "2d010000aa003c07", 0x0C0C0318, "2d010000aa001001"),
        ("LDAC remote 44.1 only", 0xFF, "2d010000aa002007", 0x0C0C0318, "2d010000aa002001"),
        ("LDAC remote 96k only", 0xFF, "2d010000aa000407", 0x0C0C0318, None),
        ("LDAC remote mono only", 0xFF, "2d010000aa003004", 0x0C0C0318, None),
        ("aptX", 0xFF, "4f00000001000000", 0x0C0C0318, None),
        ("codec 4", 4, "0102030405060708", 0x0C0C0318, None),
    ]
    for name, ctype, elems, want_pc, want_buf in cases:
        if elems:
            codec_struct(e, rem, ctype, bytes.fromhex(elems), rel)
        e.wr(buf, b"\0" * 10)
        e.wr(cfg, bytes([ctype, 8]) + struct.pack("<I", buf) + b"\0" * 6)
        regs = list(SENT)
        regs[2], regs[3], regs[8] = rem, ctype, cfg
        pc, err = e.run(SITES["H4"], [0x0C0C0318, 0x0C0C00B2], regs)
        check(err is None and pc == want_pc, f"H4 {name}: resumes at {want_pc:#x} (got {pc}, {err})")
        check(e.regs() == regs, f"H4 {name}: r0-r12 preserved")
        got = e.rd(buf, 10)
        if want_buf:
            check(got == bytes.fromhex(want_buf) + b"\0\0", f"H4 {name}: config {got.hex()}")
            allowed = [(buf, buf + 8)]
        else:
            check(got == b"\0" * 10, f"H4 {name}: config untouched")
            allowed = []
        bad = e.writes_outside(allowed)
        check(not bad, f"H4 {name}: unexpected writes {bad[:5]}")


def test_h5(e):
    print("H5 stream open hook")
    ctx = e.ctx_base
    ev, cs, el = 0x20103000, 0x20103100, 0x20103200
    stock_sbc_written = bytes([0x20, 0x10, 0x77, 0x01]) + b"\x77" * 7 + bytes([0x00])  # +3f..+4a as the stock left it
    cases = [
        ("LDAC 48k open (ev 2)", 2, 0xFF, "2d010000aa001001", 0x0C0C05FC, (0xFF, 0x10, 0x10, 2)),
        ("LDAC 44.1k open (ev 1f)", 0x1F, 0xFF, "2d010000aa002001", 0x0C0BFCB0, (0xFF, 0x20, 0x10, 2)),
        ("SBC open (ev 2)", 2, 0x00, "3fff0235", 0x0C0C05FC, None),
        ("aptX open (ev 2)", 2, 0xFF, "4f0000000100ff", 0x0C0C05FC, None),
    ]
    for name, event, ctype, elems, want_pc, want in cases:
        e.wr(ctx + 0x3f, stock_sbc_written)
        codec_struct(e, cs, ctype, bytes.fromhex(elems), el)
        e.wr(ev, bytes([event]) + b"\0" * 11 + struct.pack("<I", cs))
        regs = list(SENT)
        regs[4], regs[5] = ctx, ev
        pc, err = e.run(SITES["H5"], [0x0C0C05FC, 0x0C0BFCB0], regs)
        check(err is None and pc == want_pc, f"H5 {name}: resumes at {want_pc:#x} (got {pc}, {err})")
        r = e.regs()
        check(r[3] == event, f"H5 {name}: r3 = event byte ({r[3]:#x})")
        check(r[:3] + r[4:] == regs[:3] + regs[4:], f"H5 {name}: other registers preserved")
        f = e.rd(ctx + 0x3f, 12)
        if want:
            got = (f[0x4a - 0x3f], f[0], f[1], f[3])
            check(got == want, f"H5 {name}: ctx +4a/+3f/+40/+42 = {got} want {want}")
            allowed = [(ctx + 0x3f, ctx + 0x4b), GAP]
        else:
            check(f == stock_sbc_written, f"H5 {name}: ctx as the stock wrote it")
            allowed = [GAP]
        bad = e.writes_outside(allowed)
        check(not bad, f"H5 {name}: unexpected writes {bad[:5]}")


def test_h6(e, S):
    print("H6 OTA debug opcode hook")
    ROLE, ENV, TX = 0x2008A060, 0x2008A844, 0x20104001
    pkt, blk = 0x20104100, 0x20104200
    sent = []
    e.extra = {TX & ~1: lambda u: sent.append(bytes(u.mem_read(u.reg_read(UC_ARM_REG_R0), u.reg_read(UC_ARM_REG_R1))))}
    e.wr(ENV + 0x98, struct.pack("<I", TX))
    TBH, DEFAULT = 0x0C0AD078, 0x0C0AD132

    def run(op, magic=b"BEST", role=2, tlv=0):
        e.wr(ROLE, bytes([role])); e.wr(ENV + 0xB0, bytes([tlv]))
        e.wr(pkt, bytes([op]) + magic + b"\0" * 8)
        sent.clear()
        regs = list(SENT)
        regs[4], regs[6] = pkt, 5
        pc, err = e.run(0x0C0AD070, [TBH, DEFAULT], regs)     # ldrb r3, [r4] ; bl hook ; bhi
        return pc, err, regs

    for op in range(0x80, 0x100):
        pc, err, regs = run(op)
        r = e.regs()
        want_pc = TBH if op <= 0x99 else DEFAULT
        ok = (err is None and pc == want_pc and r[:3] + r[4:] == regs[:3] + regs[4:] and
              (op == 0xA5 or r[3] == op - 0x80) and (op == 0xA5 or not sent))
        check(ok, f"H6 opcode {op:#x}: {'tbh' if op <= 0x99 else 'stock exit'}, r3/regs as the stock "
                  f"(pc {pc}, r3 {r[3]:#x}, sent {len(sent)}, {err})")
    g_last = S["g_last"]
    saved = e.rd(S["ldac_swap_mode"], 1), e.rd(S["ldac_cfg_flag"], 1)
    e.wr(g_last, b"\0" * 4)
    e.wr(S["ldac_swap_mode"], b"\x02"); e.wr(S["ldac_cfg_flag"], b"\x01")
    pc, err, _ = run(0xA5)
    check(pc == DEFAULT and sent == [bytes([0xA6, 0, 2, 1])], f"H6 no stream: reply {[x.hex() for x in sent]}")
    stats = bytes(range(120))
    e.wr(blk, b"\0" * 0x34 + b"\0" * 32 + stats)            # blk_t: lastframe, lib_allocs[8], stats
    e.wr(g_last, struct.pack("<I", blk))
    pc, err, _ = run(0xA5)
    want = bytes([0xA6, 120]) + stats[:2] + bytes([2, 1]) + stats[4:]
    check(pc == DEFAULT and sent == [want], f"H6 stream: reply {[x[:8].hex() for x in sent]}")
    for label, kw in (("wrong magic", {"magic": b"BESX"}), ("role 3", {"role": 3}), ("TLV mode", {"tlv": 1})):
        pc, err, _ = run(0xA5, **kw)
        check(pc == DEFAULT and not sent, f"H6 {label}: no reply, stock exit ({pc}, {len(sent)})")
    bad = e.writes_outside([GAP, (ROLE, ROLE + 1), (ENV + 0xB0, ENV + 0xB1), (pkt, pkt + 16)])
    check(not bad, f"H6: unexpected writes {bad[:5]}")
    e.extra = {}
    e.wr(g_last, b"\0" * 4)
    e.wr(S["ldac_swap_mode"], saved[0]); e.wr(S["ldac_cfg_flag"], saved[1])   # H3 test reads them


def test_h3(e, S):
    print("H3 decoder table hook")
    ctx = e.ctx_base
    src, dst = 0x2001CA80, 0x200B0338
    pattern = bytes(range(0x70))
    ready = "ldac_table" in S          # real decoder linked (stub has no ldac_table)
    ldac_tbl = e.rd(S["ldac_table"], 0x70) if ready else None
    for name, codec, expect_sbc in [("SBC stream", 0x00, True),
                                    ("LDAC stream", 0xFF, not ready)]:
        e.wr(src, pattern)
        e.wr(dst, b"\xee" * 0x70)
        e.wr(ctx + 0x4a, bytes([codec]))
        regs = list(SENT)
        regs[0], regs[1], regs[2] = dst, src, 0x70
        pc, err = e.run(SITES["H3"], [SITES["H3"] + 4], regs)
        check(err is None and pc == SITES["H3"] + 4, f"H3 {name}: returns after the site ({err})")
        got = e.rd(dst, 0x70)
        check(got == pattern if expect_sbc else got == ldac_tbl, f"H3 {name}: {'SBC' if expect_sbc else 'LDAC'} table copied")
        check(e.u.reg_read(UC_ARM_REG_R0) == dst, f"H3 {name}: returns dst like memcpy")
        check(e.regs()[4:12] == regs[4:12], f"H3 {name}: r4-r11 preserved")
        bad = e.writes_outside([(dst, dst + 0x70), GAP])   # + ldac_swap_mode
        check(not bad, f"H3 {name}: unexpected writes {bad[:5]}")


def main():
    img = open(sys.argv[1], "rb").read()
    S = syms(sys.argv[2])
    e = Emu(img)
    test_h1(e, S)
    test_h2(e, S)
    test_h4(e)
    test_h5(e)
    test_h6(e, S)
    test_h3(e, S)
    print(f"{'ALL PASS' if not failures else f'{len(failures)} FAILURES'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
