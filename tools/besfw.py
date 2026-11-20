#!/usr/bin/env python3
"""BES OTA image tool for Fairbuds 1 (BES1501P): emulate the device's final
OTA check (the 0x88 -> 0x84 answer), patch bytes, and rebuild the trailer CRC.

The device check was read from two places that agree:
  * stock fw 6.5, ota_check_image_data_sanity_crc at 0x2C0ACCE8..0x2C0ACF2C
    (file offset 0x8CCE8; literal pool 0x8CF48 -> "CRC32_OF_IMAGE=0x")
  * OpenPineBuds SDK lib_ibrt_ota_RTX.a, ota_bes_handle_received_data+0x7e2

On 0x88 the device:
  1. reads the last 0x200 bytes of the received image and looks for the
     17-byte keyword "CRC32_OF_IMAGE=0x" (first hit, offsets 0..0x1FF);
  2. parses the next 8 chars as hex (0-9a-fA-F; anything else -> 0xFF nibble);
  3. compares that with crc32(image[0 : kw_off + 17])  ("data sanity crc");
     the 8 hex digits themselves are NOT covered;
  4. rewrites the first word to 0xBE57EC1C (NORMAL_BOOT start word);
  5. compares crc32(FF FF FF FF + image[4:]) with the CRC sent in 0x80.
Any failure -> 0x84 00. All pass -> 0x84 01, then 0x92 commits.

Usage:
  besfw.py check IMAGE [--crc HEX]       emulate the device check
  besfw.py patch IN OUT OFF:OLD:NEW ...  patch bytes (hex), fix trailer, check
  besfw.py selftest                      check pass/fail on known images
"""
import sys
import zlib

KW = b"CRC32_OF_IMAGE=0x"
TAIL = 0x200
START_WORD_PLACEHOLDER = b"\xff\xff\xff\xff"


def crc32(data, crc=0):
    return zlib.crc32(data, crc) & 0xFFFFFFFF


def find_keyword(img):
    """Offset of the keyword as the device finds it, or None."""
    base = len(img) - TAIL
    for loc in range(TAIL):
        if img[base + loc:base + loc + len(KW)] == KW:
            return base + loc
    return None


def device_hex8(chars):
    """The device's hex parser: 8 chars, MSB first, bad char -> 0xFF nibble."""
    val = 0
    shift = 28
    for c in chars:
        if 0x30 <= c <= 0x39:
            d = c - 0x30
        elif 0x61 <= c <= 0x66:
            d = c - 0x57
        elif 0x41 <= c <= 0x46:
            d = c - 0x37
        else:
            d = 0xFF
        val |= (d << shift)
        shift -= 4
    return val & 0xFFFFFFFF


def sanity(img):
    """(ok, stored, calculated, kw_off) of the device's data sanity crc."""
    kw = find_keyword(img)
    if kw is None:
        return False, None, None, None
    stored = device_hex8(img[kw + len(KW):kw + len(KW) + 8])
    calc = crc32(img[:kw + len(KW)])
    return stored == calc, stored, calc, kw


def whole_crc_device(img):
    """CRC the device computes in step 5 (first word forced to FFFFFFFF)."""
    return crc32(img[4:], crc32(START_WORD_PLACEHOLDER))


def device_check(img, crc80=None):
    """Return (ok, list of messages). crc80 defaults to crc32(file), which
    is what the Fairbuds app sends in 0x80."""
    msgs = []
    ok = True
    if crc80 is None:
        crc80 = crc32(img)
    if img[:4] != START_WORD_PLACEHOLDER:
        msgs.append(f"WARN first word {img[:4].hex()} != ffffffff: "
                    "file CRC != device whole CRC")
    s_ok, stored, calc, kw = sanity(img)
    if kw is None:
        msgs.append("FAIL keyword not in last 0x200 bytes")
        ok = False
    else:
        msgs.append(f"{'ok  ' if s_ok else 'FAIL'} sanity: kw@0x{kw:X} "
                    f"stored=0x{stored:08X} calc=0x{calc:08X} "
                    f"text={img[kw + len(KW):kw + len(KW) + 8]!r}")
        ok &= s_ok
    w = whole_crc_device(img)
    w_ok = w == crc80
    msgs.append(f"{'ok  ' if w_ok else 'FAIL'} whole: device=0x{w:08X} "
                f"0x80=0x{crc80:08X}")
    ok &= w_ok
    return ok, msgs


def fix_trailer(img):
    """Rewrite the 8 hex digits after the keyword with the right CRC."""
    img = bytearray(img)
    kw = find_keyword(img)
    if kw is None:
        raise ValueError("keyword not found in tail")
    pos = kw + len(KW)
    old = bytes(img[pos:pos + 8])
    if not all(c in b"0123456789ABCDEFabcdef" for c in old):
        raise ValueError(f"unexpected trailer digits {old!r}")
    img[pos:pos + 8] = b"%08X" % crc32(img[:pos])
    return bytes(img)


def patch(img, specs):
    img = bytearray(img)
    for spec in specs:
        off, old, new = spec.split(":")
        off = int(off, 16)
        old, new = bytes.fromhex(old), bytes.fromhex(new)
        if len(old) != len(new):
            raise ValueError(f"{spec}: old/new length differ")
        cur = bytes(img[off:off + len(old)])
        if cur != old:
            raise ValueError(f"{spec}: found {cur.hex()} at 0x{off:X}, expected {old.hex()}")
        kw = find_keyword(img)
        if off + len(old) > kw:
            raise ValueError(f"{spec}: patch reaches the trailer")
        img[off:off + len(new)] = new
    return fix_trailer(bytes(img))


def selftest():
    import os
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    stock = open(os.path.join(root, "apk/fw_fairbuds_6_5.bin"), "rb").read()
    results = []

    def expect(name, img, want, crc80=None):
        ok, msgs = device_check(img, crc80)
        good = ok == want
        results.append(good)
        print(f"{'PASS' if good else 'BAD '} {name}: device says {'0x84 01' if ok else '0x84 00'}"
              f" (expected {'0x84 01' if want else '0x84 00'})")
        if not good:
            for m in msgs:
                print("      ", m)

    expect("stock 6.5", stock, True)
    expect("stock 6.5, idempotent fix_trailer", fix_trailer(stock), True)
    assert fix_trailer(stock) == stock, "fix_trailer changed a good image"
    sbc_only = os.path.join(root, "patched/fw_sbc_only.bin")
    if os.path.exists(sbc_only):
        expect("fw_sbc_only.bin (stale trailer; app test C gave 0x84 00)",
               open(sbc_only, "rb").read(), False)
    old_xq = os.path.join(root, "patched/fw_fairbuds_6_5_sbcxq.bin")
    if os.path.exists(old_xq):
        expect("old sbcxq (trailer written at kw+19; app test B gave 0x84 00)",
               open(old_xq, "rb").read(), False)
    xq = patch(stock, ["14423B:27:35"])
    expect("new sbcxq", xq, True)
    # sabotage: every changed region must be caught
    for off in (0x14423B, 0x1000, find_keyword(xq) + len(KW)):
        bad = bytearray(xq)
        bad[off] ^= 0x01
        expect(f"sabotage new sbcxq byte 0x{off:X}", bytes(bad), False)
    expect("new sbcxq with stale 0x80 crc", xq, False, crc80=crc32(stock))
    print("selftest", "OK" if all(results) else "FAILED")
    return all(results)


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[1]
    if cmd == "selftest":
        return 0 if selftest() else 1
    if cmd == "check":
        img = open(argv[2], "rb").read()
        crc80 = int(argv[argv.index("--crc") + 1], 16) if "--crc" in argv else None
        ok, msgs = device_check(img, crc80)
        print(f"{argv[2]}: size={len(img)} crc32(file)=0x{crc32(img):08X}")
        for m in msgs:
            print("  ", m)
        print("  => device would answer", "0x84 01" if ok else "0x84 00")
        return 0 if ok else 1
    if cmd == "patch":
        img = open(argv[2], "rb").read()
        out = patch(img, argv[4:])
        ok, msgs = device_check(out)
        for m in msgs:
            print("  ", m)
        if not ok:
            print("refusing to write: image would fail the device check")
            return 1
        open(argv[3], "wb").write(out)
        print(f"wrote {argv[3]} size={len(out)} crc32(file)=0x{crc32(out):08X}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
