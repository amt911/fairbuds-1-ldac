"""Find the Bluetooth address of the paired Fairbuds (BlueZ), so no address is hard-coded."""
import re
import subprocess


def fairbuds_addr(addr=None):
    """Return addr if given, else the single paired device whose name contains "Fairbuds"."""
    if addr:
        return addr
    out = subprocess.run(["bluetoothctl", "devices", "Paired"], capture_output=True, text=True).stdout
    if not out.strip():
        out = subprocess.run(["bluetoothctl", "devices"], capture_output=True, text=True).stdout
    found = [m.group(1) for m in re.finditer(r"Device ([0-9A-F:]{17}) .*Fairbuds", out, re.I)]
    if len(found) != 1:
        raise SystemExit(f"found {len(found)} paired Fairbuds ({', '.join(found) or 'none'}): pass --addr XX:XX:XX:XX:XX:XX")
    return found[0]
