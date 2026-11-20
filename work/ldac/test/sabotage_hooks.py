#!/usr/bin/env python3
"""Every sabotaged image must make emu_hooks.py fail. Usage: sabotage_hooks.py IMAGE BLOB.elf"""
import os, subprocess, sys, tempfile
img = bytearray(open(sys.argv[1], "rb").read())
elf = sys.argv[2]
here = os.path.dirname(os.path.abspath(__file__))
CODE = 0x0C020000
nm = subprocess.run(["llvm-nm", elf], capture_output=True, text=True).stdout
S = {p[2]: int(p[0], 16) for p in (l.split() for l in nm.splitlines()) if len(p) == 3}

def blob_find(pat, start_sym):
    base = (S[start_sym] & ~1) - CODE
    i = img.find(pat, base)
    assert i >= 0 and i < base + 0x200, pat.hex()
    return i

def variant(name, edit):
    v = bytearray(img); edit(v)
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        f.write(v); path = f.name
    r = subprocess.run([sys.executable, os.path.join(here, "emu_hooks.py"), path, elf], capture_output=True, text=True)
    os.unlink(path)
    caught = r.returncode != 0
    print(f"{'caught ' if caught else 'MISSED '} {name}: {r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-200:]}")
    return caught

def h2_cmp(v):     # the flag-producing (last) `cmp.w r8, #0` in ldac_cfg_hook -> #1
    base = (S["ldac_cfg_hook"] & ~1) - CODE
    i = img.rfind(bytes.fromhex("b8f1000f"), base, base + 0x40); assert i >= 0; v[i + 2] = 1
def h2_flagclear(v):  # the first `cmp.w r8, #0` (SBC -> clear ldac_cfg_flag) -> #1
    i = blob_find(bytes.fromhex("b8f1000f"), "ldac_cfg_hook"); v[i + 2] = 1
def h4_ret(v):     # literal 0x0C0C0319 -> 0x0C0C031B
    i = img.find((0x0C0C0319).to_bytes(4, "little"), (S["ldac_cfginit_hook"] & ~1) - CODE); v[i] = 0x1B
def caps(v):       # advertised sampling frequencies 0x30 -> 0x3C
    i = (S["ldac_caps"]) - CODE; v[i + 6] = 0x3C
def h3_target(v):  # H3 BL -> ldac_boot_hook instead of ldac_dec_copy_hook
    sys.path.insert(0, os.path.join(here, "../../tools")); import build_ldac_image as b
    o = 0x0C127876 - CODE; v[o:o + 4] = b.bl(0x0C127876, S["ldac_boot_hook"] & ~1)

def h5_cmp(v):     # `cmp r3, #2` inside ldac_open_hook -> #3
    i = blob_find(bytes.fromhex("2b78022b"), "ldac_open_hook"); v[i + 2] = 3

def h6_op(v):      # `cmp r3, #0xa5` in ldac_dbg_hook -> #0xa6 (request never answered)
    i = blob_find(bytes.fromhex("a52b"), "ldac_dbg_hook"); v[i] = 0xA6
def h6_exit(v):    # `movs r3, #0xff` -> #0x8e (after a reply, the stock would run its 0x8E case)
    i = blob_find(bytes.fromhex("ff23"), "ldac_dbg_hook"); v[i] = 0x8E

ok = all([variant("H6 opcode", h6_op), variant("H6 exit to stock default", h6_exit), variant("H5 flags", h5_cmp), variant("H2 SBC clears LDAC flag", h2_flagclear), variant("H2 flags", h2_cmp), variant("H4 return address", h4_ret),
          variant("LDAC caps", caps), variant("H3 BL target", h3_target)])
print("all sabotages caught" if ok else "SOME SABOTAGE MISSED")
sys.exit(0 if ok else 1)
