"""Does motion change the frame timing? Alternate standstill and motion and
compare the EasyCAT's frame arrival after SYNC0 per phase.

Phases, repeated --rounds times (the delta powered and homed throughout):
- idle: standstill for --idle s;
- square: the square with dips (+-50 mm, dip -15, corner 14) at 50 %
  (F 1000, ACC 100000, JERK 400000) for --move s, streamed;
- idle again;
- fast: the same path at 80 % (F 1600, ACC 160000, JERK 640000).

The EasyCAT measures every frame's arrival after SYNC0 (ESP32 interrupt
on the output SM event, MCPWM timestamps); the PLC logs min / max per
100 ms (SYS ESP_ARR). The PLC ms at each phase boundary comes from the
newest log bucket, so the buckets are split by phase exactly. Per phase:
arrival min / max, the medians of the bucket min and max, and the spread
(bucket max - min) median / max. Motion phases also get the drives' late
% (DEM_STATS reset at the phase start).

    python tools/arrival_phases.py --owner-ok [--rounds 3] [--idle 5] [--move 10]
"""

import argparse
import json
import time

import machine as mc
from machine import log

SQUARE = [(-50, -50), (50, -50), (50, 50), (-50, 50)]


def path(f, acc, jerk, laps):
    kin = dict(F=f, ACC=acc, DEA=acc, JERK=jerk)
    pk = []
    for _ in range(laps):
        for x, y in SQUARE:
            pk.append(dict(kin, type="M", cmd="G1", X=x, Y=y, Z=0.0, Cor=14.0))
            pk.append(dict(kin, type="M", cmd="G1", X=x, Y=y, Z=-15.0, Cor=14.0))
            pk.append({"type": "M", "cmd": "G4", "P": 0.01})
            pk.append(dict(kin, type="M", cmd="G1", X=x, Y=y, Z=0.0, Cor=0.0))
    return pk


def now_ms():
    r = mc.sys_cmd("ESP_ARR", **{"from": 0})
    n = r["n"]
    if n == 0:
        return 0
    ev = mc.sys_cmd("ESP_ARR", **{"from": n - 1})["ev"].rstrip(",")
    return int(ev.split(":")[0]) + 100


def home():
    mc.plc(dict(type="M", cmd="G1", X=0.0, Y=0.0, Z=0.0, F=200.0, ACC=2000.0, DEA=2000.0, JERK=20000.0, Cor=0.0))
    mc.plc({"type": "M", "cmd": "WAIT_FOR_MOTION_STOP", "timeout": 30, "timeout_ms": 12000}, 13000)


def dem_counts():
    """Cumulative (late, moving) per drive. DEM_STATS reset:1 would also
    clear the arrival log, so the phases take differences instead."""
    dm = mc.sys_cmd("DEM_STATS")
    out = []
    for k in range(3):
        h = [int(x) for x in dm["dl%d" % k].rstrip(",").split(",")]
        out.append((dm["dlate%d" % k], sum(h[1:])))
    return out


def move(pkts, seconds):
    c0 = dem_counts()
    mc.push("plc_stream_start", {"pkts": pkts, "timeoutMs": 30000})
    t0 = time.time()
    while time.time() - t0 < seconds:
        time.sleep(0.5)
        if not mc.push("plc_stream_status", {})["running"]:
            break
    mc.push("plc_send_many_abort", {})
    while mc.push("plc_stream_status", {})["running"]:
        time.sleep(0.2)
    c1 = dem_counts()
    return [round(100.0 * (c1[k][0] - c0[k][0]) / max(1, c1[k][1] - c0[k][1]), 2) for k in range(3)]


def read_log():
    n = mc.sys_cmd("ESP_ARR", **{"from": 0})["n"]
    rows, i = [], max(0, n - 4096)
    while i < n:
        ev = mc.sys_cmd("ESP_ARR", **{"from": i})["ev"].rstrip(",")
        vals = [tuple(int(x) for x in e.split(":")) for e in ev.split(",") if e]
        if not vals:
            break
        rows += vals
        i += len(vals)
    return rows


def stats(rows):
    if not rows:
        return None
    lo = sorted(r[1] for r in rows)
    hi = sorted(r[2] for r in rows)
    sp = sorted(r[2] - r[1] for r in rows)
    med = lambda v: v[len(v) // 2]
    return {"buckets": len(rows), "min": lo[0], "max": hi[-1], "p50_min": med(lo), "p50_max": med(hi),
            "spread_p50": med(sp), "spread_max": sp[-1]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--idle", type=float, default=5)
    ap.add_argument("--move", type=float, default=10)
    ap.add_argument("--owner-ok", action="store_true")
    a = ap.parse_args()
    mc.require_owner_ok(a.owner_ok)
    mc.reconnect()
    mc.set_delta(real=True)
    mc.fsm_to("Ready", timeout=180)
    mc.sys_cmd("DEM_STATS", reset=1)        # once: it also clears the arrival log
    phases = []
    slow = path(1000.0, 100000.0, 400000.0, 40)
    fast = path(1600.0, 160000.0, 640000.0, 60)
    try:
        for r in range(a.rounds):
            for name, kind in (("idle", None), ("square", slow), ("idle", None), ("fast", fast)):
                t0 = now_ms()
                if kind is None:
                    time.sleep(a.idle)
                    late = None
                else:
                    late = move(kind, a.move)
                t1 = now_ms()
                phases.append({"round": r + 1, "phase": name, "ms": (t0, t1), "late_pct": late})
                log("round %d %-6s PLC ms %d..%d%s" % (r + 1, name, t0, t1,
                                                       "" if late is None else "  late %% %s" % late))
                if kind is not None:
                    home()
    finally:
        try:
            home()
        except Exception:
            pass
        for _ in range(5):
            try:
                mc.set_delta(real=False)
                break
            except Exception:
                time.sleep(3)
    rows = read_log()
    print()
    print("%-5s %-6s %7s %6s %6s %8s %8s %9s %9s  %s" % (
        "round", "phase", "buckets", "min", "max", "p50 min", "p50 max", "spread50", "spreadmx", "late % EAxis0/1/2"))
    out = []
    for p in phases:
        t0, t1 = p["ms"]
        st = stats([x for x in rows if t0 + 100 <= x[0] and x[0] + 100 <= t1])
        out.append(dict(p, arrival=st))
        if st:
            print("%-5d %-6s %7d %6d %6d %8d %8d %9d %9d  %s" % (
                p["round"], p["phase"], st["buckets"], st["min"], st["max"], st["p50_min"], st["p50_max"],
                st["spread_p50"], st["spread_max"], "" if p["late_pct"] is None else "/".join(map(str, p["late_pct"]))))
    for name in ("idle", "square", "fast"):
        st = stats([x for p in phases if p["phase"] == name
                    for x in rows if p["ms"][0] + 100 <= x[0] and x[0] + 100 <= p["ms"][1]])
        if st:
            log("all %-6s: arrival %d..%d us, bucket p50 %d..%d, spread p50 %d / max %d us (%d buckets)" % (
                name, st["min"], st["max"], st["p50_min"], st["p50_max"], st["spread_p50"], st["spread_max"], st["buckets"]))
    json.dump(out, open("../codesys_scripts/jobs/arrival_phases.json", "w"), indent=1)


if __name__ == "__main__":
    main()
