#!/usr/bin/env python3
"""PC OTA client for Fairbuds 1 (BES, SPP/RFCOMM channel 13), mirroring the Fairbuds app flow
that is verified to work (work/logs/app_ota_*_pixel.log):

  0x97 01 -> 0x98 | 0x8E BEST -> 0x8F | 0x90 00 -> 0x91 01 | 0x8C BEST+code+crc -> 0x8D
  0x80 BEST size crc -> 0x81 (MTU) | 0x86 config -> 0x87 01
  data: 0x85 + payload (MTU-9 B, cut at 32768-B segment ends); after each segment
        0x82 BEST crc(segment) and WAIT for 0x83 01
  0x88 -> 0x84 01, then (only then) 0x92 BEST -> 0x93 01

Differences from the app, on purpose:
  * config 0x86 flags default to 0: no user-data wipe, no rename, no BT/BLE address rewrite
    (the app always sends 0x1f). Use --app-flags to send exactly what the app sends.
  * 0x9D / 0x9B are not sent (the device never answers them; the app waits 10 s for each).
Safety:
  * the image must pass work/tools/besfw.py device_check (what the device checks on 0x88);
  * 0x92 (commit) is sent only after 0x84 01; any other answer aborts without committing;
  * a non-zero resume breakpoint in 0x8D aborts (resume is not implemented).

Usage: python3 pc_ota3.py IMAGE [--addr XX:XX:XX:XX:XX:XX] [--dry-run] [--no-commit]
(--addr defaults to the single paired device named "Fairbuds")
"""
import argparse
import os
import socket
import struct
import sys
import threading
import time
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import besfw  # noqa: E402
from btaddr import fairbuds_addr  # noqa: E402

CH = 13
SEG = 32768
# The app also sends the BT/BLE addresses here, but they are only applied with the config flags it
# uses (0x1f: rename, rewrite addresses, wipe user data). This client always sends flags 0, so the
# device ignores these fields: send zeros, never another device's address.
BLE_ADDR_LE = bytes(6)


def crc32(b):
    return zlib.crc32(b) & 0xFFFFFFFF


def build_config(img, flags):
    start = img[-4:-1] + b"\x00"  # app: last 3 bytes of the image file, 4th byte 0 -> 0x00020000
    body = (struct.pack("<I", 88) + start + struct.pack("<I", flags)
            + b"Fairbuds".ljust(32, b"\0") + b"Fairbuds".ljust(32, b"\0")
            + BLE_ADDR_LE + BLE_ADDR_LE)
    assert len(body) == 88
    return body + struct.pack("<I", crc32(body))


class Link:
    def __init__(self, addr, log):
        self.log = log
        self.s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        self.s.settimeout(20)
        self.s.connect((addr, CH))
        self.buf = bytearray()
        self.lock = threading.Lock()
        self.alive = True
        threading.Thread(target=self._rx, daemon=True).start()

    def _rx(self):
        self.s.settimeout(0.2)
        while self.alive:
            try:
                d = self.s.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not d:
                break
            with self.lock:
                self.buf += d
            self.log(f"RX {d.hex()}")
        self.alive = False

    def tx(self, pkt, label):
        self.log(f"TX {label} {pkt[:24].hex()}{'...' if len(pkt) > 24 else ''} ({len(pkt)} B)")
        self.s.sendall(pkt)

    def expect(self, opcode, timeout):
        """Wait for a response packet starting with opcode; return it (bytes after it included)."""
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                i = self.buf.find(bytes([opcode]))
                if i >= 0:
                    d = bytes(self.buf[i:])
                    del self.buf[:]
                    return d
            if not self.alive:
                return None
            time.sleep(0.005)
        return None

    def close(self):
        self.alive = False
        self.s.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--addr", default=None, help="earbuds BT address (default: paired \"Fairbuds\")")
    ap.add_argument("--dry-run", action="store_true", help="check image and print plan only")
    ap.add_argument("--no-commit", action="store_true", help="stop after 0x84 01, never send 0x92")
    ap.add_argument("--pace-ms", type=float, default=8.0, help="delay between data packets")
    a = ap.parse_args()

    img = open(a.image, "rb").read()
    ok, msgs = besfw.device_check(img)
    for m in msgs:
        print("  ", m)
    if not ok:
        print("REFUSING: the device would answer 0x84 00 for this image")
        return 2
    flags = 0                      # never rename / rewrite addresses / wipe user data
    cfg = build_config(img, flags)
    crc = crc32(img)
    print(f"image {a.image} size={len(img)} crc=0x{crc:08X} config flags=0x{flags:02x}")
    if a.dry_run:
        print("config:", cfg.hex())
        return 0

    logf = open(f"pc_ota3_{time.strftime('%Y%m%d_%H%M%S')}.log", "w")
    t0 = time.time()

    def log(msg):
        line = f"{time.time() - t0:8.3f} {msg}"
        logf.write(line + "\n")
        logf.flush()
        if not msg.startswith("TX 0x85") and not msg.startswith("RX 8301"):
            print(line, flush=True)

    L = Link(fairbuds_addr(a.addr), log)
    try:
        L.tx(b"\x97\x01", "0x97")
        L.expect(0x98, 3)
        L.tx(b"\x8eBEST", "0x8E")
        if not L.expect(0x8F, 8):
            raise SystemExit("no 0x8F")
        L.tx(b"\x90\x00", "0x90")
        r = L.expect(0x91, 8)
        if not r or r[1:2] != b"\x01":
            raise SystemExit(f"side selection failed: {r!r}")
        code = b"\x00" * 32 + bytes([1, 2, 3, 4])
        L.tx(b"\x8cBEST" + code + struct.pack("<I", crc32(code)), "0x8C")
        r = L.expect(0x8D, 8)
        if not r or len(r) < 5:
            raise SystemExit(f"no 0x8D: {r!r}")
        bp = struct.unpack_from("<I", r, 1)[0]
        if bp != 0:
            raise SystemExit(f"device wants to resume at 0x{bp:x}: not supported, aborting")
        L.tx(b"\x80BEST" + struct.pack("<II", len(img), crc), "0x80")
        r = L.expect(0x81, 8)
        if not r or len(r) < 11:
            raise SystemExit(f"no 0x81: {r!r}")
        mtu = struct.unpack_from("<H", r, 9)[0]
        payload = mtu - 9
        log(f"MTU {mtu} -> payload {payload}")
        if not 100 <= payload <= 1000:
            raise SystemExit(f"unexpected MTU {mtu}")
        L.tx(b"\x86" + cfg, "0x86")
        r = L.expect(0x87, 8)
        if not r or r[1:2] != b"\x01":
            raise SystemExit(f"config rejected: {r!r}")

        off = 0
        while off < len(img):
            seg_end = min(off + SEG, len(img))
            seg_start = off
            while off < seg_end:
                chunk = img[off:min(off + payload, seg_end)]
                L.tx(b"\x85" + chunk, f"0x85 @{off}")
                off += len(chunk)
                time.sleep(a.pace_ms / 1000)
            L.tx(b"\x82BEST" + struct.pack("<I", crc32(img[seg_start:seg_end])), f"0x82 {seg_start}-{seg_end}")
            r = L.expect(0x83, 15)
            if not r or r[1:2] != b"\x01":
                raise SystemExit(f"segment {seg_start}-{seg_end} not verified: {r!r}")
            print(f"segment ok {seg_end}/{len(img)} ({100 * seg_end / len(img):.0f}%) {time.time() - t0:.0f}s", flush=True)

        L.tx(b"\x88", "0x88")
        r = L.expect(0x84, 30)
        if not r or r[1:2] != b"\x01":
            raise SystemExit(f"final check failed: {r!r} -> NOT committing")
        if a.no_commit:
            print("0x84 01 received; --no-commit: stopping before 0x92")
            return 0
        L.tx(b"\x92BEST", "0x92")
        r = L.expect(0x93, 30)
        print("COMMIT", "OK" if r and r[1:2] == b"\x01" else f"answer {r!r}")
        return 0 if r and r[1:2] == b"\x01" else 1
    finally:
        L.close()
        logf.close()


if __name__ == "__main__":
    sys.exit(main())
