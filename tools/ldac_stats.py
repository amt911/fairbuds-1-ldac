#!/usr/bin/env python3
"""Read the LDAC add-on statistics over the OTA SPP channel (RFCOMM 13), no audio involved.

Request A5 'B' 'E' 'S' 'T'; reply A6, n, then n bytes of ldac_stats_t (ldac/src/addon.h),
n = 0 when no LDAC stream is open (then 2 bytes follow: swap_mode, cfg_flag).
Only use it while no OTA is running.  Usage:
  python3 ldac_stats.py [--addr AA:BB:..] [--interval 1.0] [--count 0] [--log FILE]
"""
import argparse, os, socket, struct, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from btaddr import fairbuds_addr  # noqa: E402

FIELDS = ("ver pool swap_mode cfg_flag sample_rate inits deinits decode_calls frames bad_frames "
          "underflows req_samples t_first t_last t_busy t_call_max t_frame_max store_calls "
          "store_frames mem_drops mtu_limits list_max list_now sync_calls sync_fails "
          "disc_samples_calls disc_packet_calls heap_free_init heap_free_min syspool_free_init "
          "factor factor_min factor_max dest_mut ram_err gap_max late_gaps slow_calls cpu_loop_ticks t_frame_sum ram_base cpu_loop_run cpu_loop_runs").split()
FMT = "<4BI2H4II3I2H2I4H4H3I3f2H4H2I2H"
SIZE = 120
assert struct.calcsize(FMT) == SIZE and len(FIELDS) == len(struct.unpack(FMT, bytes(SIZE)))


DBG_SIZE = 76


def parse_debug(reply):
    """A7 reply (black box, ldac_dbg_t in ldac/src/addon.h) -> dict."""
    if len(reply) < 2 + DBG_SIZE or reply[0] != 0xA7 or reply[1] != DBG_SIZE:
        raise ValueError(f"bad debug reply {reply[:8].hex()}")
    b = reply[2:2 + DBG_SIZE]
    ver, n_ev = b[0], b[1]
    bad_hdr = b[2:6]
    evs = []
    for i in range(4):
        e = b[8 + 12 * i: 20 + 12 * i]
        evs.append({"hook": e[0], "type": e[1], "len": e[2], "applied": e[3], "elements": e[4:12].hex()})
    init_fs = struct.unpack_from("<2I", b, 56)
    prev = struct.unpack_from("<3I", b, 64)
    order = [evs[(n_ev - 1 - k) & 3] for k in range(min(n_ev, 4))]      # newest first
    return {"ver": ver, "n_ev": n_ev, "events_newest_first": order, "bad_hdr": bad_hdr.hex(),
            "init_fs_last_prev": init_fs, "prev_frames": prev[0], "prev_bad": prev[1], "prev_underflows": prev[2]}


def parse(reply):
    """reply bytes (starting with A6) -> dict, or {'open': False, ...} when no stream."""
    if len(reply) < 4 or reply[0] != 0xA6:
        raise ValueError(f"bad reply {reply[:8].hex()}")
    n = reply[1]
    if n == 0:
        return {"open": False, "swap_mode": reply[2], "cfg_flag": reply[3]}
    if n != SIZE or len(reply) < 2 + SIZE:
        raise ValueError(f"bad stats size {n} / {len(reply)}")
    d = dict(zip(FIELDS, struct.unpack(FMT, reply[2:2 + SIZE])))
    d["open"] = True
    return d


def request(sock, magic=b"BEST"):
    sock.send(b"\xa5" + magic)
    buf = b""
    t_end = time.time() + 3
    while time.time() < t_end:
        try:
            buf += sock.recv(512)
        except socket.timeout:
            break
        if len(buf) >= 2 and buf[0] == 0xA6 and len(buf) >= (2 + buf[1] if buf[1] else 4):
            return buf
        if len(buf) >= 2 and buf[0] == 0xA7 and len(buf) >= 2 + buf[1]:
            return buf
    raise TimeoutError(f"no complete reply (got {buf.hex()})")


def summary(prev, cur):
    if not cur["open"]:
        return f"no LDAC stream (swap_mode {cur['swap_mode']}, cfg_flag {cur['cfg_flag']})"
    s = (f"fs {cur['sample_rate']} pool {cur['pool']} inits {cur['inits']} deinits {cur['deinits']} | "
         f"calls {cur['decode_calls']} frames {cur['frames']} bad {cur['bad_frames']} "
         f"underflows {cur['underflows']} | list {cur['list_now']}/{cur['list_max']} "
         f"memdrop {cur['mem_drops']} mtu {cur['mtu_limits']} sync {cur['sync_calls']}/{cur['sync_fails']} "
         f"disc {cur['disc_samples_calls']}/{cur['disc_packet_calls']} | heap {cur['heap_free_min']}/{cur['heap_free_init']} "
         f"| tmax call {cur['t_call_max']} frame {cur['t_frame_max']} "
         f"| factor {cur['factor']:.5f} [{cur['factor_min']:.5f}..{cur['factor_max']:.5f}] "
         f"| gap_max {cur['gap_max']} late {cur['late_gaps']} slow {cur['slow_calls']} cpu_loop {cur['cpu_loop_ticks']} "
         f"ram {cur['ram_base']:#x} err {cur['ram_err']} cpu_run {cur['cpu_loop_run']}x{cur['cpu_loop_runs']}")
    if prev and prev.get("open") and prev["inits"] == cur["inits"]:
        dt = (cur["t_last"] - prev["t_last"]) & 0xFFFFFFFF
        db = (cur["t_busy"] - prev["t_busy"]) & 0xFFFFFFFF
        ds = (cur["req_samples"] - prev["req_samples"]) & 0xFFFFFFFF
        if dt and ds:
            hz = dt / (ds / cur["sample_rate"])
            dfr = (cur["t_frame_sum"] - prev["t_frame_sum"]) & 0xFFFFFFFF
            s += (f" | load {100 * db / dt:.1f}% (ldacBT_decode {100 * dfr / dt:.1f}%) timer~{hz:.0f} Hz, "
                  f"d_underflows {cur['underflows'] - prev['underflows']}")
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--addr", default=None, help="earbuds BT address (default: paired \"Fairbuds\")")
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--count", type=int, default=0, help="0 = until Ctrl-C")
    ap.add_argument("--log")
    ap.add_argument("--debug", action="store_true", help="print the black box (config events, last streams) and exit")
    a = ap.parse_args()
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    s.settimeout(10)
    s.connect((fairbuds_addr(a.addr), 13))
    s.settimeout(0.5)
    if a.debug:
        import json
        print(json.dumps(parse_debug(request(s, b"BESD")), indent=1))
        s.close()
        return
    log = open(a.log, "a") if a.log else None
    prev, i = None, 0
    try:
        while a.count == 0 or i < a.count:
            raw = request(s)
            cur = parse(raw)
            print(f"{time.strftime('%H:%M:%S')} {summary(prev, cur)}", flush=True)
            if log:
                log.write(f"{time.time():.3f} {raw.hex()}\n"); log.flush()
            prev, i = cur, i + 1
            time.sleep(a.interval)
    finally:
        s.close()


if __name__ == "__main__":
    main()
