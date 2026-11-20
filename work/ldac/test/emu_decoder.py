#!/usr/bin/env python3
"""End-to-end test of the LDAC decoder blob in Unicorn (Cortex-M33 + FPU).

A sine is encoded on the host with AOSP libldac (libldacBT_enc.so, as the phone would), packed in
A2DP media payloads (1-byte frame-count header + LDAC frames), fed to the blob's decoder table
(store_packet) and decoded (decode_frame) by the real ARM code: ldac_lib.a + ldac_decoder.c +
the stock libgcc helpers in the stock RAM copy. Framework calls (heap, list, syspool,
lastframe) are stubbed in Python. Checks: decoded PCM matches the sine (SNR), return codes,
no write outside allowed memory, underflow behaviour, list limiter.

Usage: work/.venv/bin/python work/ldac/test/emu_decoder.py IMAGE.bin ADDON.elf
"""
import ctypes
import math
import struct
import subprocess
import sys

from unicorn import Uc, UcError, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_PROT_ALL
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../tools"))
from ldac_stats import parse as parse_stats  # noqa: E402  (layout shared with the PC reader)
from unicorn import arm_const as A

CODE, DATA = 0x0C020000, 0x2C020000
RAM, RAMX = 0x20000000, 0x00200000
HEAP0, HEAP_SZ = 0x20100000, 0x40000
SYS0, SYS_SZ = 0x20140000, 0x10000
CTX, CFG, HDR, PAY, OUT = 0x20150000, 0x20150200, 0x20150300, 0x20150400, 0x20151000
STACK = 0x20170000
RET = 0x0FFFFFF0
LIST = 0x2E000000
NODE0 = 0x2F000000
STUBS = {
    0x0027B340: "heap_malloc", 0x0027B2E4: "heap_free", 0x0C126B14: "heap_info",
    0x0C126CBC: "list_append", 0x0C126C5C: "list_remove", 0x0C126BC4: "list_length",
    0x0C126B98: "list_begin", 0x0C126C30: "list_node", 0x0C127D68: "lastframe_set",
    0x00271FB8: "syspool_get_buff", 0x00271FA4: "syspool_free_size",
    0x0C0C2858: "aac_setup", 0x0C103444: "btif_register", 0x0C13B4B8: "trace",
    0x0C123938: "sysfreq_req",
}
FAIL = []


def check(c, m):
    if not c:
        FAIL.append(m)
        print("   FAIL", m)


def nm(elf):
    out = subprocess.run(["llvm-nm", elf], capture_output=True, text=True, check=True).stdout
    return {p[2]: int(p[0], 16) for p in (l.split() for l in out.splitlines()) if len(p) == 3}


class Fw:
    def __init__(self, img):
        u = self.u = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        u.ctl_set_cpu_model(A.UC_CPU_ARM_CORTEX_M33)
        size = (len(img) + 0xFFFF) & ~0xFFFF
        for base in (CODE, DATA):
            u.mem_map(base, size)
            u.mem_write(base, img)
        # RAM and its RAMX instruction alias are the same memory, as on the chip: the LDAC library
        # runs from a relocated copy in the A2DP heap through the RAMX alias (ldac_ram.h).
        self._ram_buf = ctypes.create_string_buffer(0x200000)
        u.mem_map_ptr(RAM, 0x200000, UC_PROT_ALL, ctypes.addressof(self._ram_buf))
        u.mem_map_ptr(RAMX, 0x200000, UC_PROT_ALL, ctypes.addressof(self._ram_buf))
        for fs, fe, vs in [(0x1788, 0x90F4, 0x20060180), (0x90F4, 0x19A74, 0x2006FE40),
                           (0x194704, 0x196ED8, 0x20080840)]:
            u.mem_write(vs, img[fs:fe])
            if vs < 0x20080000:
                u.mem_write(vs - RAM + RAMX, img[fs:fe])
        u.mem_map(RET & ~0xFFF, 0x1000)
        u.mem_map(0x2E000000, 0x2000000)           # fake list/node handles (never dereferenced)
        # stock system timer (down-counter at 0x40002004): every read advances it by 3 ticks
        u.mem_map(0x40002000, 0x1000)
        self.timer = 0
        self.timer_reads = 0
        u.hook_add(UC_HOOK_MEM_READ, self._timer, begin=0x40002004, end=0x40002007)
        # Stubs are real `bx lr` instructions; the code hook only sets r0. Redirecting PC from inside
        # a Unicorn 2.1.4 code hook was unreliable: an instruction after the return was skipped
        # (ldac_init, `ldr.w r0, [r10]` after heap_has) or the redirect was ignored and the real
        # stock function ran (list_begin -> RTOS mutex -> SVC). Traced 2026-11-19.
        for a in STUBS:
            u.mem_write(a, b"\x70\x47")
        self.heap = HEAP0
        self.live = {}            # ptr -> size (A2DP heap, first-fit with reuse)
        self.freed = []           # (ptr, size)
        self.sys = SYS0
        self.sys_calls = []
        self.heap_cap = HEAP_SZ
        self.sys_cap = SYS_SZ
        self.list = []
        self.node_of = {}
        self.last = None
        self.allowed = []
        u.hook_add(UC_HOOK_CODE, self._code)
        # No UC_HOOK_MEM_WRITE: in Unicorn 2.1.4 a global write hook breaks the IT state after a
        # conditional store (`it lo; strlo.w` in heap_has): the next block ran with a stale IT
        # (0x38) and skipped `ldr.w r0, [r10]` after the return (traced 2026-11-19; single-step
        # and no-write-hook runs are correct). Stray writes are found by diffing RAM instead.

    def _ret(self, v=0):
        self.u.reg_write(A.UC_ARM_REG_R0, v & 0xFFFFFFFF)   # the stub's `bx lr` returns

    def _code(self, u, addr, size, _):
        name = STUBS.get(addr)
        if not name:
            return
        r = [u.reg_read(x) for x in (A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2)]
        if name == "heap_malloc":
            self._ret(self.malloc(r[0]))
        elif name == "heap_free":
            self.free(r[0])
            self._ret(0)
        elif name == "heap_info":
            used = sum(self.live.values())
            for ptr, val in zip(r, (self.heap_cap, used, used)):
                if ptr:
                    u.mem_write(ptr, struct.pack("<I", val))
            self._ret(0)
        elif name == "syspool_free_size":
            self._ret(max(0, SYS0 + self.sys_cap - self.sys))
        elif name == "syspool_get_buff":
            n = (r[1] + 3) & ~3
            free = SYS0 + SYS_SZ - self.sys
            if n > free:
                FAIL.append("syspool exhausted (stock would ASSERT)")
                u.emu_stop()
            self.sys_calls.append(r[1])
            u.mem_write(r[0], struct.pack("<I", self.sys))
            self.sys += n
            self._ret(free)
        elif name == "list_append":
            self.list.append(r[1])
            self._ret(1)
        elif name == "list_remove":
            if r[1] in self.list:
                self.list.remove(r[1])
                # the stock list free callback calls the decoder's packet_free: frame->buf, frame
                buf = struct.unpack("<I", u.mem_read(r[1] + 0xC, 4))[0]
                self.free(buf)
                self.free(r[1])
            self._ret(1)
        elif name == "list_length":
            self._ret(len(self.list))
        elif name == "list_begin":
            if not self.list:
                self._ret(0)
            else:
                node = NODE0 + 0x10 * (self.list[0] & 0xFFFFF)
                self.node_of[node] = self.list[0]
                self._ret(node)
        elif name == "list_node":
            self._ret(self.node_of.get(r[0], 0))
        elif name == "sysfreq_req":
            self.sysfreq = (r[0], r[1])
            self._ret(0)
        elif name == "lastframe_set":
            self.last = bytes(u.mem_read(r[0], 0x34))
            self._ret(0)
        else:
            self._ret(0)

    def _timer(self, u, access, addr, size, value, _):
        self.timer = (self.timer - 3) & 0xFFFFFFFF
        self.timer_reads += 1
        u.mem_write(0x40002004, struct.pack("<I", self.timer))

    def fresh_heap(self):
        # the stock creates the A2DP heap again for every stream (a2dp_audio_init)
        self.list.clear(); self.live.clear(); self.freed.clear(); self.heap = HEAP0

    def malloc(self, n):
        if getattr(self, "misalign", False):
            # A2DP heap blocks are only guaranteed 4-aligned: return 8k+4 addresses
            p = self._malloc(n + 4)
            self.live[p + 4] = self.live.pop(p)
            return p + 4
        return self._malloc(n)

    def _malloc(self, n):
        n = (n + 7) & ~7
        for i, (p, sz) in enumerate(self.freed):
            if sz >= n:
                del self.freed[i]
                self.live[p] = sz
                return p
        p = self.heap
        self.heap += n
        if self.heap > HEAP0 + HEAP_SZ:
            FAIL.append("heap exhausted (stock would ASSERT)")
            self.u.emu_stop()
        self.live[p] = n
        return p

    def free(self, p):
        if p in self.live:
            sz = self.live.pop(p)
            self.freed.append((p & ~7, sz))
        elif p:
            FAIL.append(f"free of unknown/double-freed pointer {p:#x}")

    def ram(self):
        return bytes(self.u.mem_read(RAM, 0x200000))

    def stray_writes(self, before):
        """Addresses whose byte changed since `before` (ram()) outside allowed memory and stack."""
        now, bad = self.ram(), []
        for off in range(0, len(now), 0x1000):
            if now[off:off + 0x1000] == before[off:off + 0x1000]:
                continue
            for i in range(off, off + 0x1000):
                a = RAM + i
                if now[i] != before[i] and not (STACK - 0x2000 <= a < STACK) and \
                        not any(lo <= a < hi for lo, hi in self.allowed):
                    bad.append(a)
        return bad

    def call(self, fn, *args, limit=400_000_000):
        u = self.u
        regs = (A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2, A.UC_ARM_REG_R3)
        for r, v in zip(regs, args):
            u.reg_write(r, v & 0xFFFFFFFF)
        u.reg_write(A.UC_ARM_REG_SP, STACK)
        u.reg_write(A.UC_ARM_REG_LR, RET | 1)
        u.emu_start(fn | 1, RET, count=limit)
        return u.reg_read(A.UC_ARM_REG_R0)


def encode_ldac(sr, seconds, freq, freq_r=None):
    L = ctypes.CDLL("libldacBT_enc.so")
    L.ldacBT_get_handle.restype = ctypes.c_void_p
    h = ctypes.c_void_p(L.ldacBT_get_handle())
    # mtu 679 (A2DP), eqmid HQ (0), stereo (0x01), S16 (2), sample rate
    if L.ldacBT_init_handle_encode(h, 679, 0, 0x01, 2, sr) != 0:
        raise SystemExit("libldac init failed")
    n = int(sr * seconds)
    pcm = bytearray()
    for i in range(n):
        v = int(12000 * math.sin(2 * math.pi * freq * i / sr))
        # right: two incommensurate tones, so its alignment is unambiguous (a single tone repeats
        # every period and a 128-sample shift can look perfect)
        w = v if freq_r is None else int(5000 * math.sin(2 * math.pi * freq_r * i / sr) +
                                         4000 * math.sin(2 * math.pi * freq_r * 1.5727 * i / sr))
        pcm += struct.pack("<hh", v, w)
    packets, off = [], 0
    step = 128 * 4
    stream = ctypes.create_string_buffer(1024)
    used, ssz, nfr = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
    while off < len(pcm):
        chunk = bytes(pcm[off:off + step]).ljust(step, b"\0")
        buf = ctypes.create_string_buffer(chunk)
        L.ldacBT_encode(h, buf, ctypes.byref(used), stream, ctypes.byref(ssz), ctypes.byref(nfr))
        off += used.value if used.value > 0 else step
        if ssz.value > 0:
            packets.append((nfr.value, stream.raw[:ssz.value]))
    L.ldacBT_free_handle(h)
    return pcm, packets


REQ_SIZES = [3072, 1000, 776, 2560, 4, 6144, 1532]


def snr_at(ref, got, lag, start, n=4000):
    r = struct.unpack("<%dh" % (len(ref) // 2), ref)[0::2]
    g = struct.unpack("<%dh" % (len(got) // 2), got)[0::2]
    if start + lag + n > len(g) or start + n > len(r):
        return -1e9
    sig = sum(r[i] * r[i] for i in range(start, start + n, 4))
    err = sum((r[i] - g[i + lag]) ** 2 for i in range(start, start + n, 4))
    return 10 * math.log10(sig / max(err, 1))


def snr_db(ref, got, ch=0):
    # best alignment over a small lag window, compare one channel (0 left, 1 right)
    r = struct.unpack("<%dh" % (len(ref) // 2), ref)[ch::2]
    g = struct.unpack("<%dh" % (len(got) // 2), got)[ch::2]
    best = (-1e9, 0)
    base = 48000            # skip the 0.6 s diagnostic start beeps
    for lag in range(0, 1200, 1):
        n = min(len(r), len(g) - lag) - base - 2000
        if n < 4000:
            break
        sig = sum(r[i] * r[i] for i in range(base, base + 4000, 4))
        err = sum((r[i] - g[i + lag]) ** 2 for i in range(base, base + 4000, 4))
        s = 10 * math.log10(sig / max(err, 1))
        if s > best[0]:
            best = (s, lag)
    return best


def stats(fw, S):
    p = fw.call(S["ldac_stats_get"])
    if not p:
        return {"open": False}
    return parse_stats(bytes([0xA6, 120]) + bytes(fw.u.mem_read(p, 120)))


def main():
    img = open(sys.argv[1], "rb").read()
    S = nm(sys.argv[2])
    fw = Fw(img)
    gap = (S["__gap_data_start"], 0x200BC800)
    fw.allowed = [gap, (HEAP0, HEAP0 + HEAP_SZ), (SYS0, SYS0 + SYS_SZ), (CTX, OUT + 0x8000)]
    ram_before = fw.ram()

    # boot hook: RAM gap init (and a stubbed SEP registration)
    ctx_base = struct.unpack_from("<I", img, 0x0C0BCA2C - CODE)[0]
    fw.u.mem_write(ctx_base + 0x34, struct.pack("<I", 0x20100000 - 0x100))
    fw.allowed.append((ctx_base, ctx_base + 0x260))
    fw.call(S["ldac_boot_hook"], 0)
    tbl = S["ldac_table"]
    fn = struct.unpack_from("<14I", bytes(fw.u.mem_read(tbl + 0x28, 56)))
    init, deinit, decode, preparse, store = fn[0], fn[1], fn[2], fn[3], fn[4]
    conv, headinfo, pfree = fn[8], fn[10], fn[12]
    check(all(x & 1 for x in fn), "all table pointers are Thumb")
    check(pfree & ~1 == S["ldac_packet_free"] & ~1 if "ldac_packet_free" in S else True, "packet_free slot")

    for sr in (48000, 44100):
        print(f"--- {sr} Hz")
        fw.fresh_heap()
        fw.misalign = sr == 44100          # 4-aligned heap blocks: the loader must align itself
        pcm, packets = encode_ldac(sr, 2.0, 1000, 1100)     # different L and R: channel order/alignment
        print(f"   encoded {len(packets)} packets, frames/packet {sorted(set(p[0] for p in packets))}, "
              f"bytes/packet {min(len(p[1]) for p in packets)}..{max(len(p[1]) for p in packets)}")
        fw.u.mem_write(CTX, b"\0" * 0x100)
        fw.u.mem_write(CTX + 0x16, struct.pack("<H", 0 if sr == 48000 else 30))   # stock passes 0 for LDAC
        fw.u.mem_write(CTX + 0xC, struct.pack("<f", 0.96875))     # framework speed factor
        fw.u.mem_write(CTX + 0x90, struct.pack("<I", LIST))
        fw.u.mem_write(CFG, struct.pack("<IBBHIf", sr, 2, 16, 0, 128, 1.0))
        sys_before, heap_before = fw.sys, sum(fw.live.values())
        fw.sysfreq = None
        check(fw.call(init, CFG, CTX) == 0, f"{sr}: init returns 0")
        want_freq = (0x1E, 8)                      # own sysfreq user, top level
        check(fw.sysfreq == want_freq, f"{sr}: sysfreq request {fw.sysfreq} (want {want_freq})")
        print(f"   syspool calls by init: {fw.sys_calls}, heap blocks live after init: {len(fw.live)}")
        fw.sys_calls = []
        print(f"   syspool used by init: {fw.sys - sys_before} B, A2DP heap used by init: {sum(fw.live.values()) - heap_before} B")
        check(fw.last is not None and struct.unpack_from("<I", fw.last, 0x24)[0] == sr,
              f"{sr}: lastframe stream_info.sample_rate")
        out = bytearray()
        seq = 0
        ncall = 0
        for nfr, payload in packets:
            fw.u.mem_write(HDR, struct.pack("<IHHI", 0, seq, 0, seq * 128 * nfr))
            fw.u.mem_write(PAY, bytes([nfr]) + payload)
            rc = fw.call(store, HDR, PAY, len(payload) + 1)
            check(rc == 0, f"{sr}: store rc {rc} seq {seq}")
            seq += 1
            while len(fw.list) >= 24:                      # drain like the audio callback
                req = REQ_SIZES[ncall % len(REQ_SIZES)]       # irregular sizes: carry buffer
                ncall += 1
                rc = fw.call(decode, OUT, req) & 0xFFFFFFFF
                check(rc == 0, f"{sr}: decode rc {rc:#x}")
                out += fw.u.mem_read(OUT, req)
        samples = ctypes.c_uint32(0)
        fw.call(conv, CFG + 0x80)
        n_samples = struct.unpack("<I", fw.u.mem_read(CFG + 0x80, 4))[0]
        check(n_samples == len(fw.list) * 128, f"{sr}: convert_list_to_samples {n_samples} vs {len(fw.list)}*128")
        snr, lag = snr_db(pcm, bytes(out))
        snr_r, lag_r = snr_db(pcm, bytes(out), ch=1)
        print(f"   right channel: SNR {snr_r:.1f} dB at lag {lag_r}")
        check(snr_r > 30 and lag_r == lag, f"{sr}: right channel SNR {snr_r:.1f} dB lag {lag_r} (left lag {lag})")
        print(f"   decoded {len(out)//4} samples, SNR {snr:.1f} dB at lag {lag}")
        check(snr > 30, f"{sr}: SNR {snr:.1f} dB")
        # continuity over the whole stream (a dropped or repeated sample breaks the alignment)
        tail = bytes(out)[len(out) - 4 * 20000:]
        snr2 = snr_at(pcm, bytes(out), lag, len(out) // 4 - 20000)
        print(f"   end-of-stream SNR at the same lag: {snr2:.1f} dB")
        check(snr2 > 30, f"{sr}: continuity, SNR {snr2:.1f} dB at the end")
        st = stats(fw, S)
        req_total = sum(REQ_SIZES[i % len(REQ_SIZES)] for i in range(ncall)) // 4
        n_frames = sum(p[0] for p in packets)
        want_mut = 120 if sr == 48000 else 30                    # 0 -> LDAC target; non-zero kept
        check(struct.unpack("<H", fw.u.mem_read(CTX + 0x16, 2))[0] == want_mut,
              f"{sr}: dest_packet_mut {struct.unpack('<H', fw.u.mem_read(CTX + 0x16, 2))[0]} (want {want_mut})")
        check(st["factor"] == st["factor_min"] == st["factor_max"] == 0.96875 and st["dest_mut"] == want_mut,
              f"{sr}: stats factor/dest_mut {({k: st[k] for k in ('factor', 'factor_min', 'factor_max', 'dest_mut')})}")
        # emulated timer: 3 ticks per read. Only the 4-byte requests (1 sample: period 0 ticks) are
        # slower than their period and arrive later than 1.5 periods; every other size is far
        # longer than the few ticks a call takes here.
        n4 = sum(1 for i in range(1, ncall) if REQ_SIZES[i % len(REQ_SIZES)] == 4)
        check(0 < st["gap_max"] and st["late_gaps"] == n4 and st["slow_calls"] == n4,
              f"{sr}: stats gaps {({k: st[k] for k in ('gap_max', 'late_gaps', 'slow_calls')})}, want {n4}")
        check(st["cpu_loop_ticks"] > 0, f"{sr}: stats cpu_loop_ticks {st['cpu_loop_ticks']}")
        check(0 < st["t_frame_sum"] < st["t_busy"], f"{sr}: stats t_frame_sum {st['t_frame_sum']} busy {st['t_busy']}")
        check(HEAP0 <= st["ram_base"] < HEAP0 + HEAP_SZ and st["ram_base"] % 8 == 0 and st["ram_err"] == 0,
              f"{sr}: stats ram_base {st['ram_base']:#x} (8-aligned, in the A2DP heap), err {st['ram_err']}")
        check(st["cpu_loop_runs"] == len(packets) // 256 and (st["cpu_loop_run"] > 0) == (st["cpu_loop_runs"] > 0),
              f"{sr}: stats cpu_loop_run {st['cpu_loop_run']} x {st['cpu_loop_runs']} ({len(packets)} stores)")
        check(st["open"] and st["ver"] == 7 and st["sample_rate"] == sr and st["pool"] == 1,
              f"{sr}: stats header {({k: st.get(k) for k in ('open', 'ver', 'sample_rate', 'pool')})}")
        check(st["decode_calls"] == ncall and st["req_samples"] == req_total,
              f"{sr}: stats calls {st['decode_calls']}/{ncall}, req_samples {st['req_samples']}/{req_total}")
        check(st["store_frames"] == n_frames and st["frames"] == n_frames - len(fw.list),
              f"{sr}: stats frames stored {st['store_frames']}/{n_frames}, decoded {st['frames']}/{n_frames - len(fw.list)}")
        check(st["bad_frames"] == 0 and st["underflows"] == 0 and st["mem_drops"] == 0 and st["mtu_limits"] == 0,
              f"{sr}: stats no errors {({k: st[k] for k in ('bad_frames', 'underflows', 'mem_drops', 'mtu_limits')})}")
        check(st["inits"] == st["deinits"] + 1 and 22 <= st["list_max"] <= 23 and 0 < st["heap_free_min"] <= st["heap_free_init"],
              f"{sr}: stats inits/deinits/list/heap {({k: st[k] for k in ('inits', 'deinits', 'list_max', 'heap_free_min', 'heap_free_init')})}")
        span = (st["t_last"] - st["t_first"]) & 0xFFFFFFFF
        check(0 < st["t_busy"] and st["t_frame_max"] > 0 and st["t_call_max"] >= st["t_frame_max"] and span > 0,
              f"{sr}: stats timing busy {st['t_busy']} frame_max {st['t_frame_max']} call_max {st['t_call_max']} span {span}")
        # underflow: empty list -> CACHE_UNDERFLOW (-5) and zeroed output, no overflow
        while fw.list:
            fw.call(decode, OUT, 512)
        fw.u.mem_write(OUT, b"\x55" * 6144)
        rc = fw.call(decode, OUT, 6144)
        got = bytes(fw.u.mem_read(OUT, 6144))
        # the carried tail of the last frame (< one frame) is played first, then silence
        check(rc == 0xFFFFFFFB and b"\x55\x55\x55\x55" not in got and got[1024:] == b"\0" * (6144 - 1024),
              f"{sr}: underflow rc {rc:#x} -> carried tail then silence")
        check(stats(fw, S)["underflows"] == 1, f"{sr}: stats underflow counted ({stats(fw, S)['underflows']})")
        fw.call(deinit)
        check(fw.sysfreq == (0x1E, 0), f"{sr}: sysfreq released at deinit ({fw.sysfreq})")
        fw.misalign = False
        check(fw.call(S["ldac_stats_get"]) == 0, f"{sr}: no stats after deinit")
        check(not fw.live, f"{sr}: heap blocks leaked after deinit: {len(fw.live)}")

    # fallbacks: small A2DP heap -> ldac_lib on the stock syspool; both small -> no decoder (pulsing tone)
    pcm, packets = encode_ldac(48000, 1.2, 1000)
    # The library itself (RAM payload, ~22 KB) always comes from the A2DP heap; only ldac_lib's
    # own allocations (16 KB) may fall back to the syspool.
    for label, hcap, scap, want_sys in (("syspool small -> heap", HEAP_SZ, 0x1000, False),
                                        ("heap fits the library only -> syspool", 0xC000, SYS_SZ, True),
                                        ("heap too small for the library -> no decoder", 0x6000, SYS_SZ, False),
                                        ("heap+syspool small -> no decoder", 0x6000, 0x1000, False)):
        fw.fresh_heap(); fw.heap_cap, fw.sys_cap = hcap, scap
        sys_before = fw.sys
        check(fw.call(init, CFG, CTX) == 0, f"{label}: init returns 0")
        used_sys = fw.sys - sys_before
        check((used_sys > 0) == want_sys, f"{label}: syspool used {used_sys}")
        for nfr, payload in packets[:60]:
            fw.u.mem_write(PAY, bytes([nfr]) + payload)
            fw.call(store, HDR, PAY, len(payload) + 1)
        out = bytearray()
        while len(fw.list) > 12:
            rc = fw.call(decode, OUT, 12 * 512)
            check(rc == 0, f"{label}: decode rc {rc:#x}")
            out += fw.u.mem_read(OUT, 12 * 512)
        if want_sys or label.startswith("syspool small"):
            print(f"   {label}: decoded {len(out)//4} samples")
            check(len(out) > 0, f"{label}: decoded audio")
        else:
            check(len(out) > 0 and not any(out), f"{label}: silence, never a tone")
            st = stats(fw, S)
            check(st["pool"] == 3 and st["bad_frames"] > 0, f"{label}: stats pool {st['pool']} bad {st['bad_frames']}")
        fw.call(deinit)
    fw.heap_cap, fw.sys_cap = HEAP_SZ, SYS_SZ

    # discards_packet(n): align to a packet start, then drop n whole packets (stock SBC semantics;
    # the TWS slave uses it to line up with the master)
    fw.fresh_heap()
    fw.call(init, CFG, CTX)
    pcm, packets = encode_ldac(48000, 0.3, 700)
    npk = [nfr for nfr, _ in packets[:8]]
    for i, (nfr, payload) in enumerate(packets[:8]):
        fw.u.mem_write(HDR, struct.pack("<IHHI", 0, 100 + i, 0, i * 128 * nfr))
        fw.u.mem_write(PAY, bytes([nfr]) + payload)
        fw.call(store, HDR, PAY, len(payload) + 1)
    first = fw.list[0]
    fw.list.remove(first); fw.free(struct.unpack("<I", fw.u.mem_read(first + 0xC, 4))[0]); fw.free(first)   # head now mid-packet
    disc_pkt = fn[5]
    rc = fw.call(disc_pkt, 2) & 0xFFFFFFFF
    fw.call(headinfo, CFG + 0x100)
    hseq, hts, hcur, htot = struct.unpack("<HxxIHH", bytes(fw.u.mem_read(CFG + 0x100, 12)))
    check(rc == 0 and hseq == 103 and hcur == 0 and len(fw.list) == sum(npk[3:]),
          f"discards_packet(2) from mid-packet: rc {rc:#x}, head seq {hseq} cur {hcur} (want 103/0), "
          f"list {len(fw.list)} (want {sum(npk[3:])})")
    rc = fw.call(disc_pkt, 50) & 0xFFFFFFFF
    check(rc == 0xFFFFFFFD and len(fw.list) == sum(npk[3:]), f"discards_packet(50): rc {rc:#x} (want -3), list kept {len(fw.list)}")
    fw.call(deinit)

    # corrupted payload image in flash: the loader's checksum refuses it -> no decoder, silence
    corrupt_at = S["ldac_payload"] + 24 + 0x400
    orig = bytes(fw.u.mem_read(corrupt_at, 1))
    fw.u.mem_write(corrupt_at, bytes([orig[0] ^ 0x10]))
    fw.fresh_heap()
    check(fw.call(init, CFG, CTX) == 0, "corrupt payload: init returns 0")
    st = stats(fw, S)
    check(st["ram_base"] == 0 and st["pool"] == 3 and st["ram_err"] == 2,
          f"corrupt payload: not loaded (ram {st['ram_base']:#x}, pool {st['pool']}, err {st['ram_err']})")
    fw.call(deinit)
    fw.u.mem_write(corrupt_at, orig)

    # list limiter: store until -4 (MTU_LIMITER), never exceed LDAC_LIST_LIMIT (400) frames,
    # and nothing below it (a 200-frame list must still be accepted: v6a restarted at ~197)
    fw.fresh_heap()
    fw.call(init, CFG, CTX)
    pcm, packets = encode_ldac(48000, 3.0, 440)
    rcs = []
    for i, (nfr, payload) in enumerate(packets[:260]):
        fw.u.mem_write(PAY, bytes([nfr]) + payload)
        rcs.append(fw.call(store, HDR, PAY, len(payload) + 1) & 0xFFFFFFFF)
    first_limit = rcs.index(0xFFFFFFFC) if 0xFFFFFFFC in rcs else None
    check(first_limit is not None and 380 <= 2 * first_limit < 400 and len(fw.list) < 400,
          f"limiter: first -4 at {first_limit} packets, list {len(fw.list)}, rc set {set(rcs)}")
    bad = fw.stray_writes(ram_before)
    check(not bad, f"writes outside allowed memory: {[hex(a) for a in bad[:5]]}")
    print("ALL PASS" if not FAIL else f"{len(FAIL)} FAILURES")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
