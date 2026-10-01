"""Compare drive command filters by the shock the motion actually feels.

The cycle stays at 1 ms; the stale targets stay. A position-command filter
in the drive (P1.068 moving average, ms) spreads each one-cycle step over
several ms. Per setting:
1. P1.068 on EAxis0/1/2 by SDO (drive EEPROM; the originals are in
   codesys_scripts/jobs/drive_params_backup.json, restored at the end);
2. delta real, home, FB_STATS and DEM_STATS reset;
3. the square with dips at --speed % (100 % = F 2000, ACC 200000,
   JERK 800000) for --seconds, streamed;
4. FB_STATS: per drive, histograms of |second difference of the actual
   position| (PUU per cycle^2) and |torque change| (0.1 % per cycle) while
   enabled, with maxima; DEM_STATS late %.

    python tools/filter_sweep.py --owner-ok --levels orig 4 8 12 [--speed 70] [--seconds 60]

A level is one value for all three drives, or "a/b/c" per drive, or
"orig" (the backup values).
"""

import argparse
import json
import time

import drive_param as dp
import machine as mc
from machine import log
from arrival_phases import path, home
from sync_shift_sweep import virtual

D2_EDGES = ["<1k", "<2k", "<5k", "<10k", "<20k", "<50k", "<100k", ">=100k"]
TQ_EDGES = ["<0.5", "<1", "<2", "<5", "<10", "<20", "<50", ">=50"]   # % per cycle


def originals():
    bk = dp.load_backup()
    return [bk.get("EAxis%d P1.068" % k, dp.read("P1.068", k)) for k in range(3)]


def set_filter(level, orig):
    vals = orig if level == "orig" else [int(x) for x in level.split("/")] if "/" in level else [int(level)] * 3
    for k in range(3):
        bk = dp.load_backup()
        bk.setdefault("EAxis%d P1.068" % k, dp.read("P1.068", k))
        json.dump(bk, open(dp.BACKUP, "w"), indent=1, sort_keys=True)
        dp.sdo(dp.STATIONS[k], dp.obj("P1.068"), vals[k])
    rb = [dp.read("P1.068", k) for k in range(3)]
    log("  P1.068 read back: %s" % rb)
    return rb


def run(pkts, seconds):
    mc.sys_cmd("FB_STATS", reset=1)
    mc.sys_cmd("DEM_STATS", reset=1)
    mc.push("plc_stream_start", {"pkts": pkts, "timeoutMs": 30000})
    t0 = time.time()
    while time.time() - t0 < seconds:
        time.sleep(1)
        if not mc.push("plc_stream_status", {})["running"]:
            break
    mc.push("plc_send_many_abort", {})
    while mc.push("plc_stream_status", {})["running"]:
        time.sleep(0.2)
    f = mc.sys_cmd("FB_STATS")
    dm = mc.sys_cmd("DEM_STATS")
    home()
    res = {}
    for k in range(3):
        fd = [int(x) for x in f["fd%d" % k].rstrip(",").split(",")]
        ft = [int(x) for x in f["ft%d" % k].rstrip(",").split(",")]
        h = [int(x) for x in dm["dl%d" % k].rstrip(",").split(",")]
        res["EAxis%d" % k] = {"d2_hist": fd, "d2_max": f["fdm%d" % k], "tq_hist": ft, "tq_max": f["ftm%d" % k],
                              "late_pct": round(100.0 * dm["dlate%d" % k] / max(1, sum(h[1:])), 2)}
    return res


def tail(hist, frm):
    n = sum(hist)
    return 1e6 * sum(hist[frm:]) / max(1, n)          # per million cycles


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--levels", nargs="+", default=["orig", "4", "8", "12"])
    ap.add_argument("--speed", type=float, default=70)
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--owner-ok", action="store_true")
    a = ap.parse_args()
    mc.require_owner_ok(a.owner_ok)
    mc.reconnect()
    orig = originals()
    log("P1.068 originals: %s" % orig)
    s = a.speed / 100.0
    pkts = path(2000.0 * s, 200000.0 * s, 800000.0 * s, int(a.seconds) + 30)
    results = []
    try:
        for lv in a.levels:
            log("=== P1.068 %s ===" % lv)
            mc.drives_off()
            rb = set_filter(lv, orig)
            mc.set_delta(real=True)
            mc.fsm_to("Ready", timeout=180)
            r = run(pkts, a.seconds)
            virtual()
            results.append({"level": lv, "readback": rb, **r})
            for k in range(3):
                x = r["EAxis%d" % k]
                log("  EAxis%d late %.2f %% | pos d2 max %d, >=20k %.0f ppm, >=50k %.0f ppm | torque change max %.1f %%, >=5 %% %.0f ppm, >=10 %% %.0f ppm" % (
                    k, x["late_pct"], x["d2_max"], tail(x["d2_hist"], 5), tail(x["d2_hist"], 6),
                    x["tq_max"] / 10.0, tail(x["tq_hist"], 4), tail(x["tq_hist"], 5)))
            print(json.dumps(results[-1]), flush=True)
    finally:
        virtual()
        log("=== restore P1.068 %s ===" % orig)
        mc.drives_off()
        set_filter("orig", orig)
    json.dump(results, open("../codesys_scripts/jobs/filter_sweep.json", "w"), indent=1)
    log("summary (per drive EAxis0/1/2): pos d2 max | torque change max %")
    for r in results:
        log("  %-8s d2 max %s | torque max %s | late %s" % (
            r["level"], "/".join(str(r["EAxis%d" % k]["d2_max"]) for k in range(3)),
            "/".join("%.1f" % (r["EAxis%d" % k]["tq_max"] / 10.0) for k in range(3)),
            "/".join("%.2f" % r["EAxis%d" % k]["late_pct"] for k in range(3))))


if __name__ == "__main__":
    main()
