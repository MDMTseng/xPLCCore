"""Direct drive test without SoftMotion: EAxis0's controlword and target
position are written by PLC code (SYS DIRECT, PRG_EventLog) while
SoftMotion holds EAxis0 as a virtual axis. A constant step per cycle up
--deg and back; the drive's position demand (0x6062) is checked against
the target every cycle (DEM_STATS). If the drive still uses stale targets
here, SoftMotion is ruled out too (doc_review/asda_stale_target_2026-09-30.md).

    python tools/direct_test.py --owner-ok [--deg 1.5] [--step 145] [--hold-only]

--hold-only enables the drive and holds position (no motion): checks that
the written controlword reaches the drive. The PLC stops the test by itself
on a drive fault, a following error over 0.5 deg, or 2 s without the
heartbeat this script sends. EAxis1 / EAxis2 stay powered off. At the end
the drive is disabled and the delta set back to virtual.
"""

import argparse
import json
import time

import machine as mc
from machine import log

PUU_PER_DEG = 1444704.7


def direct(**kw):
    return mc.sys_cmd("DIRECT", **kw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deg", type=float, default=1.5)
    ap.add_argument("--step", type=int, default=145, help="PUU per 1 ms cycle (145 = 0.1 deg/s)")
    ap.add_argument("--hold-only", action="store_true")
    ap.add_argument("--owner-ok", action="store_true")
    a = ap.parse_args()
    mc.require_owner_ok(a.owner_ok)
    if a.deg > 3:
        raise SystemExit("--deg is capped at 3")
    mc.reconnect()
    mc.drives_off()
    # EAxis0 virtual in SoftMotion (SoftMotion leaves the drive alone), the others real and off
    mc.rpc_write("GVL.AxisSimConfigMask", 1)
    mc.sys_cmd("SET_AXIS_SIM", mask=1)
    for _ in range(40):
        if mc.delta_mask() == 1:
            break
        time.sleep(0.5)
    if mc.delta_mask() != 1:
        raise SystemExit("could not make EAxis0 alone virtual (mask %s)" % mc.delta_mask())
    log("EAxis0 virtual in SoftMotion, FSM", mc.fsm()[0])

    mc.sys_cmd("DEM_STATS", reset=1)
    travel = 0 if a.hold_only else int(a.deg * PUU_PER_DEG)
    r = direct(start=1, step=a.step, travel=max(travel, 1), maxfe=int(0.5 * PUU_PER_DEG))
    log("DIRECT start:", r)
    t0 = time.time()
    expect = 15 + (2 * travel / max(1, a.step) / 1000.0)
    try:
        last = None
        while time.time() - t0 < expect + 20:
            r = direct(hb=1)   # the heartbeat (the PLC stops after 2 s without one)
            if r["state"] != last:
                log("state %s %s  sw 0x%04X cw 0x%04X  target %d actual %d" % (
                    r["state"], r["reason"], r["sw"], r["cw"], r["target"], r["actual"]))
                last = r["state"]
            if r["state"] in (5, 99):
                break
            if a.hold_only and r["state"] == 4:
                time.sleep(2)
                break
            time.sleep(0.4)
    finally:
        r = direct(stop=1)
        log("DIRECT stop:", {k: r[k] for k in ("state", "reason", "cycles", "sw")})
    dm = mc.sys_cmd("DEM_STATS")
    h = [int(x) for x in dm["dl0"].rstrip(",").split(",")]
    moving = sum(h[1:])
    out = {"test": "direct hold" if a.hold_only else "direct %.2f deg step %d" % (a.deg, a.step),
           "hist": h, "late": dm["dlate0"], "stale": dm["ds0"], "d2max": dm["dmx0"],
           "late_pct": round(100.0 * dm["dlate0"] / max(1, moving), 2), "nsnap": dm["nsnap"]}
    print(json.dumps(out), flush=True)
    for i in range(min(2, dm["nsnap"])):
        s = mc.sys_cmd("DEM_STATS", snap=i)
        if s["s_axis"] != 0:
            continue
        T = [int(x) for x in (s["s_t0"] + s["s_t1"]).rstrip(",").split(",")]
        D = [int(x) for x in (s["s_d0"] + s["s_d1"]).rstrip(",").split(",")]
        log("snapshot %d sent  dT:" % i, " ".join("%4d" % (T[j] - T[j - 1]) for j in range(10, 30)))
        log("snapshot %d drive dD:" % i, " ".join("%4d" % (D[j] - D[j - 1]) for j in range(10, 30)))
    mc.set_delta(real=False)
    log("delta back to virtual")


if __name__ == "__main__":
    main()
