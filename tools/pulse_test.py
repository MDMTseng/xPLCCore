"""Constant-pulse test for one delta drive, for the ASDA stale-target issue
(doc_review/asda_stale_target_2026-09-30.md). EAxis<axis> moves alone
(FSM Powered, group off: no planner, no kinematics, no blending), up <dist>
deg at a constant speed and back. 0.1 deg/s is ~144.5 increments per 1 ms
cycle.

Checked at the same time:
- the drive's own position demand (0x6062 readback, SYS DEM_STATS): lag
  histogram, late = one cycle behind the normal lag (a stale target),
  demand d2, snapshots;
- the PLC side (EC_STATS), with the integer glitch threshold lowered to
  --thresh for the test: the Target Position PDO in the output image and
  diSetPosition.

    python tools/pulse_test.py [--axis 0] [--dist 3] [--vel 0.1] [--owner-ok]

Through the UI's link (standalone UI with XPLC_HARNESS=1). A real delta
needs --owner-ok (machine.py).
"""

import argparse
import time

import machine as mc
from machine import log


def stats(label, k):
    e = mc.sys_cmd("EC_STATS")
    log("%-10s set-int glitch %s max|d2| %s | tap glitch %s max|d2| %s =now %s =prev %s neither %s | "
        "EasyCAT stale %s skip %s | lost %s" % (
            label, e["dg%d" % k], e["dd%d" % k], e["tg%d" % k], e["td%d" % k], e["tnow%d" % k],
            e["tprev%d" % k], e["tnone%d" % k], e["e_stale"], e["e_skip"], e["lost"]))
    dm = mc.sys_cmd("DEM_STATS")
    log("%-10s drive demand 0x6062: stale %s late %s d2 glitch %s max %s snaps %s | lag hist (0..6,none) %s" % (
        "", dm["ds%d" % k], dm["dlate%d" % k], dm["dgl%d" % k], dm["dmx%d" % k], dm["nsnap"],
        dm["dl%d" % k].rstrip(",")))
    return e


def move(k, dist, vel):
    r = mc.sys_cmd("JOINT_MOVE", axis=k, dist=dist, vel=vel)
    t0 = time.time()
    log("JOINT_MOVE EAxis%d %+g deg at %g deg/s (%.1f s)" % (k, dist, r.get("vel", vel), abs(dist) / vel))
    end = t0 + abs(dist) / vel + 15
    while time.time() < end:
        time.sleep(5)
        js = mc.sys_cmd("JOINT_STATE")
        stats("  %3.0f s" % (time.time() - t0), k)
        if js["s%d" % k] != 1:
            log("  EAxis%d state %s pos %.4f" % (k, js["s%d" % k], js["p%d" % k]))
            return
    mc.sys_cmd("JOINT_STOP")
    raise SystemExit("timeout: stopped")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--axis", type=int, default=0)
    ap.add_argument("--dist", type=float, default=3.0)
    ap.add_argument("--vel", type=float, default=0.1)
    ap.add_argument("--thresh", type=int, default=50, help="PLC glitch threshold during the test (increments)")
    ap.add_argument("--owner-ok", action="store_true", help="the owner OK'd moving the real delta")
    a = ap.parse_args()
    if not mc.is_delta_virtual():
        mc.require_owner_ok(a.owner_ok)
    mc.fsm_to("Powered")
    js = mc.sys_cmd("JOINT_STATE")
    log("EAxis%d %s at %.4f deg" % (a.axis, "virtual" if js["v%d" % a.axis] else "REAL", js["p%d" % a.axis]))
    mc.rpc_write("GVL.DiGlitchThresh", a.thresh)
    try:
        mc.sys_cmd("EC_STATS", reset=1)
        mc.sys_cmd("DEM_STATS", reset=1)
        time.sleep(1)
        stats("standstill", a.axis)
        move(a.axis, a.dist, a.vel)
        move(a.axis, -a.dist, a.vel)
        stats("end", a.axis)
    finally:
        mc.rpc_write("GVL.DiGlitchThresh", 150000)


if __name__ == "__main__":
    main()
