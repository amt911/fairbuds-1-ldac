#!/usr/bin/env python3
"""Play a file on the LDAC sink while polling ldac_stats every 0.25 s; kill playback at the first
anomaly (late gap, slow call, bad frame, underflow after start, decoder restart) and on ANY error of
the guard itself (fail safe: no stats -> no sound). Optional 3rd arg: log file of raw replies.
Usage: guarded_play.py SINK FILE [LOG]   (SINK: `pactl list short sinks`, the bluez_output.* one)
Use it for the first listening tests of a new build: earbuds on the table, low volume."""
import os, socket, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ldac_stats import parse, request  # noqa: E402
from btaddr import fairbuds_addr  # noqa: E402

sink, wav = sys.argv[1], sys.argv[2]
log = open(sys.argv[3], "w") if len(sys.argv) > 3 else None
s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
s.settimeout(10)
s.connect((fairbuds_addr(os.environ.get("FAIRBUDS_ADDR")), 13))
s.settimeout(0.5)
p = subprocess.Popen(["paplay", "-d", sink, wav])
t0 = time.time()
base = last = None
reason = None
try:
    while p.poll() is None:
        try:
            raw = request(s)
            d = parse(raw)
        except Exception as e:
            reason = f"stats unavailable ({e})"
            break
        if log:
            log.write(f"{time.time() - t0:.2f} {raw.hex()}\n")
        if d["open"]:
            if base is None:
                base = d
            elif d["inits"] != base["inits"]:
                reason = f"decoder restart (inits {base['inits']}->{d['inits']})"
            elif d["late_gaps"] > base["late_gaps"]:
                reason = f"late gap ({d['late_gaps']})"
            elif d["slow_calls"] > base["slow_calls"]:
                reason = f"slow call ({d['slow_calls']})"
            elif d["bad_frames"] > base["bad_frames"]:
                reason = f"bad frames ({d['bad_frames']})"
            elif d["underflows"] > base["underflows"] and time.time() - t0 > 2:
                reason = f"underflow ({d['underflows']})"
            last = d
        if reason:
            break
        time.sleep(0.25)
except BaseException as e:
    reason = reason or f"guard error ({e!r})"
finally:
    if p.poll() is None:
        p.kill()
    p.wait()
    s.close()
    if log:
        log.close()
if reason:
    print(f"ABORTED at {time.time() - t0:.1f}s: {reason}")
if last:
    print(f"end {time.time() - t0:.1f}s: inits {last['inits']} calls {last['decode_calls']} frames {last['frames']} "
          f"bad {last['bad_frames']} underflows {last['underflows']} late {last['late_gaps']} slow {last['slow_calls']} "
          f"tmax {last['t_call_max']} cpu_run {last['cpu_loop_run']} list {last['list_now']}/{last['list_max']}")
print("clean" if not reason else "NOT clean")
