"""LDAC at 48, 44.1 and 96 kHz through the decoder in Unicorn: correctness (per-channel SNR),
instructions per frame (counted on 12 frames) and memory, to estimate the load on the earbuds.
Usage: .venv/bin/python ldac/test/emu_rates.py IMAGE.bin ADDON.elf"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import emu_decoder as m  # noqa: E402
from unicorn import UC_HOOK_CODE  # noqa: E402

IMG, ELF = sys.argv[1], sys.argv[2]
img = open(IMG, "rb").read()
S = m.nm(ELF)
fw = m.Fw(img)
ctx_base = struct.unpack_from("<I", img, 0x0C0BCA2C - m.CODE)[0]
fw.u.mem_write(ctx_base + 0x34, struct.pack("<I", 0x20100000 - 0x100))
fw.call(S["ldac_boot_hook"], 0)
fn = struct.unpack_from("<14I", bytes(fw.u.mem_read(S["ldac_table"] + 0x28, 56)))
init, deinit, decode, store = fn[0], fn[1], fn[2], fn[4]
results = {}
for sr in (48000, 44100, 96000):
    fw.fresh_heap()
    fw.u.mem_write(m.CTX, b"\0" * 0x100)
    fw.u.mem_write(m.CTX + 0x16, struct.pack("<H", 120))
    fw.u.mem_write(m.CTX + 0x90, struct.pack("<I", m.LIST))
    fw.u.mem_write(m.CFG, struct.pack("<IBBHIf", sr, 2, 16, 0, 128, 1.0))
    rc = fw.call(init, m.CFG, m.CTX)
    heap_init = sum(fw.live.values())
    pcm, packets = m.encode_ldac(sr, 2.0, 1000, 1100)
    fsamp = 256 if sr > 48000 else 128
    out = bytearray()
    counted = None
    count = [0]
    for i, (nfr, payload) in enumerate(packets):
        fw.u.mem_write(m.HDR, struct.pack("<IHHI", 0, i, 0, i * fsamp * nfr))
        fw.u.mem_write(m.PAY, bytes([nfr]) + payload)
        src = fw.call(store, m.HDR, m.PAY, len(payload) + 1) & 0xFFFFFFFF
        if src != 0:
            print(f"{sr}: store rc {src:#x} at packet {i}")
            break
        while len(fw.list) >= 16:
            nb = 4 * fsamp * 4                      # 4 frames per call
            if counted is None and i > 20:          # count 12 frames once the decoder is warm
                h = fw.u.hook_add(UC_HOOK_CODE, lambda u, a, s, _: count.__setitem__(0, count[0] + 1))
                for _ in range(3):
                    fw.call(decode, m.OUT, nb)
                    out += fw.u.mem_read(m.OUT, nb)
                fw.u.hook_del(h)
                counted = count[0] / 12
                continue
            fw.call(decode, m.OUT, nb)
            out += fw.u.mem_read(m.OUT, nb)
    st = m.stats(fw, S)
    snr_l, lag = m.snr_db(pcm, bytes(out))
    snr_r, lag_r = m.snr_db(pcm, bytes(out), ch=1)
    fw.call(deinit)
    results[sr] = (counted, fsamp)
    print(f"{sr} Hz: init rc {rc}, heap at init {heap_init} B, frame {fsamp} samples, "
          f"{counted:,.0f} instructions/frame -> {counted * sr / fsamp / 1e6:.1f} M instr/s, "
          f"SNR L {snr_l:.1f} dB R {snr_r:.1f} dB (lags {lag}/{lag_r}), bad {st['bad_frames']}, "
          f"frames {st['frames']}")
r48 = results[48000][0] * 48000 / 128
r96 = results[96000][0] * 96000 / 256
print(f"96k/48k cost per second of audio: {r96 / r48:.2f}x -> device load ~{21.5 * r96 / r48:.0f} % "
      f"(48k measured on the earbuds: ~21.5 % at 208 MHz)")
