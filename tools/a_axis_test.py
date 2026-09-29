"""The group's A axis on the machine (open-loop stepper on the QEC
driver's second axis): rotate it through the axis group and check the
commanded position, the move times and the peak speed / acceleration
against the axis limits.

    python tools/a_axis_test.py [--plc 192.168.1.70]

G1 A is in degrees; the PLC divides by 10 for the kinematics and the axis
scaling (6400 steps = 36 u) turns 1 u into 10 degrees, so the axis
position in u is A / 10. Open loop: the PLC cannot see lost steps; watch
the motor, or run a home-switch check. Only the A axis and the (virtual)
delta arms are commanded. Close the UI's PLC link first.
"""

import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plc_direct import Plc, Nak  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RPC = [sys.executable, os.path.join(REPO, "codesys_scripts", "rpc.py")]
AX = "SM_Drive_GenericDSP402"


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def rpc(*args):
    r = subprocess.run(RPC + list(args), capture_output=True, text=True)
    return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr.strip()


def num(s):
    s = s.split("#")[-1]
    try:
        return float(s)
    except ValueError:
        return s


def axis():
    out = {k: num(rpc("read", "%s.%s" % (AX, k))) for k in ("fSetPosition", "fActPosition", "bError", "nAxisState")}
    out["peak_vel"] = num(rpc("read", "GVL.MotionPeakVel[3]"))
    out["peak_acc"] = num(rpc("read", "GVL.MotionPeakAcc[3]"))
    rpc("logout")
    return out


def to_ready(p):
    for _ in range(80):
        r = p.sys("GA_EV", ev=0)
        st = r["st_str"]
        if st == "Ready":
            return
        if st == "Error":
            log("FSM Error:", r.get("err_src"), r.get("err_id"))
        ev = {"UnInited": 2, "Powered": 4, "GroupEnabled": 7, "Error": 8}.get(st)
        if ev is not None:
            try:
                p.sys("GA_EV", ev=ev)
            except Nak as e:
                log("GA_EV", ev, e)
        time.sleep(0.5)
    raise RuntimeError("FSM did not reach Ready")


def move(p, label, **kw):
    t0 = time.time()
    p.m("G1", **kw)
    p.m("WAIT_FOR_MOTION_STOP", timeout=30, timeout_ms=25000)
    dt = time.time() - t0
    a = axis()
    log("%-34s %.3f s | A set %7.3f u (%6.1f deg) act %7.3f u | error %s | peaks since reset: v %.1f u/s  a %.1f u/s^2" % (
        label, dt, a["fSetPosition"], a["fSetPosition"] * 10, a["fActPosition"], a["bError"],
        a["peak_vel"], a["peak_acc"]))
    return dt, a


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plc", default="192.168.1.70")
    a = ap.parse_args()
    with Plc(a.plc) as p:
        ms = p.sys("GET_MACHINE_STATE")
        if (ms.get("axes_sim_mask", 0) & 7) != 7:
            raise SystemExit("REFUSED: the delta arms are not all simulated")
        to_ready(p)
        log("limits: v %s  a %s  j %s u (x10 deg)" % tuple(
            num(rpc("read", "%s.%s" % (AX, k))) for k in ("fSWMaxVelocity", "fSWMaxAcceleration", "fSWMaxJerk")))
        p.m("SetCoord1")
        move(p, "start pose X0 Y0 Z12 A0", X=0.0, Y=0.0, Z=12.0, A=0.0, F=200, ACC=20000, DEA=20000, JERK=80000, Cor=0)
        rpc("write", "GVL.MotionPeakReset", "TRUE")
        rpc("logout")
        for target in (90, 0, 270, -90, 0):
            move(p, "A -> %d deg (A only)" % target, A=float(target))
        move(p, "X 0->30 mm + A 0->90 (one G1)", X=30.0, A=90.0, F=1000, ACC=100000, DEA=100000, JERK=400000)
        move(p, "X 30->0 mm, A stays", X=0.0, F=1000, ACC=100000, DEA=100000, JERK=400000)
        move(p, "A back to 0", A=0.0)
        p.sys("GA_EV", ev=8)
        time.sleep(1)
        log("left in", p.sys("GA_EV", ev=0)["st_str"])


if __name__ == "__main__":
    main()
