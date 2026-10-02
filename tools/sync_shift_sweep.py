"""Sweep the ASDA drives' "DC sync0 shift time" and measure the stale-target
rate at each level (the drives' SYNC0 fires this much later than the other
slaves'; the frame then has more time before the drives' SYNC0).

Per level:
1. set the shift on EAxis0/1/2 (jobs/templates/set_drive_sync_shift.py) and
   download with the drives off (machine.safe_install);
2. read back CoE 0x1C32:03 (shift time in effect, ns) from each drive;
3. delta real, home;
4. continuous Z 0 <-> -5 mm at F 5 for --seconds (G1 + G4 1 ms, no polling);
5. DEM_STATS (late = one cycle behind) and the burst count (DEM_EVT).
At the end the shift goes back to --restore (default 0) and is downloaded.

    python tools/sync_shift_sweep.py --owner-ok [--levels 100 200 300 400] [--seconds 60]

The value is written as the device parameter (CODESYS shows it in us); the
1C32:03 readback tells the unit the drive actually got.
"""

import argparse
import json
import os
import re
import time

import machine as mc
from machine import log

JOB = os.path.join(mc.REPO, "codesys_scripts", "jobs", "templates", "set_drive_sync_shift.py")
STATIONS = (1004, 1005, 1006)    # EAxis0/1/2 since the 2026-10-02 slave reorder


def set_shift(value):
    src = open(JOB, encoding="ascii").read()
    tmp = os.path.join(mc.REPO, "codesys_scripts", "jobs", "_tmp_sync_shift.py")
    open(tmp, "w", encoding="ascii").write(re.sub(r'^SHIFT = ".*"$', 'SHIFT = "%s"' % value, src, count=1, flags=re.M))
    try:
        out = mc.rpc("exec", "--file", tmp, timeout=300)
    finally:
        os.remove(tmp)
    for line in out:
        if "shift time" in line:
            log("  " + line)
    mc.safe_install(read_log=False)


def sdo_read(station, index, sub, size):
    seq = mc.sys_cmd("DRV_SDO", station=station, index=index, sub=sub, size=size)["seq_req"]
    for _ in range(50):
        r = mc.sys_cmd("DRV_SDO_RESULT")
        if not r["active"] and r["seq_res"] == seq:
            return r["value"] if r["ok"] else "err %s" % r["sdo_err"]
        time.sleep(0.2)
    return "no result"


def run_z(seconds):
    kin = dict(F=5.0, ACC=50.0, DEA=50.0, JERK=500.0, Cor=0.0)
    pkts = []
    for _ in range(int(seconds) + 20):
        for z in (-5.0, 0.0):
            pkts.append(dict(kin, type="M", cmd="G1", X=0.0, Y=0.0, Z=z))
            pkts.append({"type": "M", "cmd": "G4", "P": 0.001})
    mc.sys_cmd("DEM_STATS", reset=1)
    t0 = time.time()
    mc.push("plc_stream_start", {"pkts": pkts, "timeoutMs": 30000})
    while time.time() - t0 < seconds:
        time.sleep(5)
        if not mc.push("plc_stream_status", {})["running"]:
            break
    mc.push("plc_send_many_abort", {})
    while mc.push("plc_stream_status", {})["running"]:
        time.sleep(0.5)
    time.sleep(3)
    mc.plc(dict(kin, type="M", cmd="G1", X=0.0, Y=0.0, Z=0.0))
    mc.plc({"type": "M", "cmd": "WAIT_FOR_MOTION_STOP", "timeout": 30, "timeout_ms": 12000}, 13000)
    dm = mc.sys_cmd("DEM_STATS")
    res = {}
    for k in range(3):
        h = [int(x) for x in dm["dl%d" % k].rstrip(",").split(",")]
        moving = sum(h[1:])
        n = mc.sys_cmd("DEM_EVT", axis=k, **{"from": 0})["n"]
        res["EAxis%d" % k] = {"hist": h, "late": dm["dlate%d" % k],
                              "late_pct": round(100.0 * dm["dlate%d" % k] / max(1, moving), 2),
                              "stale": dm["ds%d" % k], "events": n}
    return res


def virtual():
    for _ in range(5):
        try:
            mc.set_delta(real=False)
            return
        except Exception:
            time.sleep(3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--levels", nargs="+", default=["100", "200", "300", "400"])
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--restore", default="0")
    ap.add_argument("--owner-ok", action="store_true")
    a = ap.parse_args()
    mc.require_owner_ok(a.owner_ok)
    mc.reconnect()
    results = {}
    try:
        for lv in a.levels:
            log("=== shift %s ===" % lv)
            mc.drives_off()
            set_shift(lv)
            mc.reconnect()
            rb = [sdo_read(st, 0x1C32, 3, 4) for st in STATIONS]
            log("  1C32:03 (ns) read back:", rb)
            mc.set_delta(real=True)
            mc.fsm_to("Ready", timeout=180)
            r = run_z(a.seconds)
            virtual()
            results[lv] = {"readback_ns": rb, **r}
            log("  late %%: %s | burst events: %s" % (
                " / ".join("%.2f" % r["EAxis%d" % k]["late_pct"] for k in range(3)),
                " / ".join(str(r["EAxis%d" % k]["events"]) for k in range(3))))
            print(json.dumps({"shift": lv, **results[lv]}), flush=True)
    finally:
        virtual()
        log("=== restore shift %s ===" % a.restore)
        mc.drives_off()
        set_shift(a.restore)
        mc.reconnect()
        log("  1C32:03 (ns) read back:", [sdo_read(st, 0x1C32, 3, 4) for st in STATIONS])
    log("summary (shift: late % EAxis0/1/2):")
    for lv, r in results.items():
        log("  %6s: %s" % (lv, " / ".join("%.2f" % r["EAxis%d" % k]["late_pct"] for k in range(3))))


if __name__ == "__main__":
    main()
