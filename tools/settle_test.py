"""Settling time of the delta's TCP after each stop, per drive filter setting.

PnP-like point-to-point moves at --speed % (100 % = F 2000, ACC 200000,
JERK 800000), each an exact stop followed by --pause s at rest: X -50 /
+50 at Z 0 with a dip to Z -15 at each end. The PLC (SYS SETTLE) takes
the stop as the moment the set positions stop changing, records the
actual TCP for 300 cycles, and reports the time until it stays within
GVL.SettleTolMm (0.2 mm) of its final position, and the distance at the
stop. Also the torque-change maximum (FB_STATS) per setting.

    python tools/settle_test.py --owner-ok --levels orig 4 8 12 [--cycles 5] [--speed 70]

Levels as in filter_sweep.py (P1.068 for all three drives, "a/b/c", or
"orig"); the originals are restored at the end.
"""

import argparse
import json
import time

import machine as mc
from machine import log
from filter_sweep import originals, set_filter
from sync_shift_sweep import virtual

POINTS = [(-50, 0, 0), (-50, 0, -15), (-50, 0, 0), (50, 0, 0), (50, 0, -15), (50, 0, 0)]


def read_settle():
    n = mc.sys_cmd("SETTLE", **{"from": 0})["n"]
    out, i = [], max(0, n - 256)
    while i < n:
        ev = mc.sys_cmd("SETTLE", **{"from": i})["ev"].rstrip(",")
        # The reply string holds 255 chars: the last entry can be cut off;
        # keep the complete ones and read the rest next time.
        vals = [tuple(int(x) for x in e.split(":")) for e in ev.split(",") if e.count(":") == 2]
        vals = [v for v in vals if len(v) == 3]
        if not vals:
            break
        out += vals
        i += len(vals)
    return out                                   # (at ms, settle ms, err at stop um)


def run(cycles, speed, pause):
    s = speed / 100.0
    kin = dict(F=2000.0 * s, ACC=200000.0 * s, DEA=200000.0 * s, JERK=800000.0 * s, Cor=0.0)
    mc.sys_cmd("SETTLE", reset=1)
    mc.sys_cmd("FB_STATS", reset=1)
    for _ in range(cycles):
        for x, y, z in POINTS:
            mc.plc(dict(kin, type="M", cmd="G1", X=float(x), Y=float(y), Z=float(z)))
            mc.plc({"type": "M", "cmd": "WAIT_FOR_MOTION_STOP", "timeout": 30, "timeout_ms": 12000}, 13000)
            time.sleep(pause)
    mc.plc(dict(kin, type="M", cmd="G1", X=0.0, Y=0.0, Z=0.0))
    mc.plc({"type": "M", "cmd": "WAIT_FOR_MOTION_STOP", "timeout": 30, "timeout_ms": 12000}, 13000)
    time.sleep(pause)
    f = mc.sys_cmd("FB_STATS")
    return read_settle(), [f["ftm%d" % k] / 10.0 for k in range(3)]


def summary(rows):
    st = sorted(r[1] for r in rows)
    er = sorted(r[2] for r in rows)
    if not st:
        return "no stops"
    q = lambda v, p: v[min(len(v) - 1, int(len(v) * p))]
    return "%d stops | settle ms p50 %d p90 %d max %d | error at stop um p50 %d max %d" % (
        len(st), q(st, 0.5), q(st, 0.9), st[-1], q(er, 0.5), er[-1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--levels", nargs="+", default=["orig", "4", "8", "12"])
    ap.add_argument("--cycles", type=int, default=5)
    ap.add_argument("--speed", type=float, default=70)
    ap.add_argument("--pause", type=float, default=0.4)
    ap.add_argument("--owner-ok", action="store_true")
    a = ap.parse_args()
    mc.require_owner_ok(a.owner_ok)
    mc.reconnect()
    orig = originals()
    log("P1.068 originals: %s; tolerance %.2f mm" % (orig, mc.sys_cmd("SETTLE", **{"from": 0})["tol"]))
    results = []
    try:
        for lv in a.levels:
            log("=== P1.068 %s ===" % lv)
            mc.drives_off()
            rb = set_filter(lv, orig)
            mc.set_delta(real=True)
            mc.fsm_to("Ready", timeout=180)
            rows, tq = run(a.cycles, a.speed, a.pause)
            virtual()
            results.append({"level": lv, "readback": rb, "stops": rows, "torque_change_max_pct": tq})
            log("  %s | torque change max %s %%" % (summary(rows), "/".join("%.1f" % x for x in tq)))
    finally:
        virtual()
        log("=== restore P1.068 %s ===" % orig)
        mc.drives_off()
        set_filter("orig", orig)
    json.dump(results, open("../codesys_scripts/jobs/settle_test.json", "w"), indent=1)
    log("summary:")
    for r in results:
        log("  %-6s %s" % (r["level"], summary(r["stops"])))


if __name__ == "__main__":
    main()
