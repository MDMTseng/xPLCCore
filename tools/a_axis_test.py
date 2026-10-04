"""The group's A axis on the machine (open-loop stepper on the QEC
driver's second axis): rotate it through the axis group and check the
commanded position, the move times and the peak speed / acceleration
against the axis limits.

    python tools/a_axis_test.py [--plc 192.168.1.70]

G1 A is in degrees. The axis' unit depends on the setup: Kin_CAxis with
the /10 wrap (6400 steps = 36 u, --deg-per-u 10, the default) or A as
additional axis (360 u per turn, --deg-per-u 1). Checked: moves beyond +-180, A together
with a path move in one G1, a G1 without A keeps A, and a queued blended
sequence (every G1 its own additional-axes instance in the PLC) ends at
the last A. Open loop: the PLC cannot see lost steps; watch the motor, or
run a home-switch check. Only the A axis and the (virtual)
delta arms are commanded. Close the UI's PLC link first.
"""

import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plc_direct import Plc, Nak  # noqa: E402
import topology as tp  # noqa: E402

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


DEG_PER_U = 10.0
A_OFFSET = 0.0   # --a-offset: axis deg minus G1 A (Kin_CAxis in SetCoord1: -600, the frame's A 60 u)


def axis():
    out = {k: num(rpc("read", "%s.%s" % (AX, k))) for k in ("fSetPosition", "fActPosition", "bError", "nAxisState")}
    out["peak_vel"] = num(rpc("read", "GVL.MotionPeakVel[3]"))
    out["peak_acc"] = num(rpc("read", "GVL.MotionPeakAcc[3]"))
    for k in ("fSetPosition", "fActPosition", "peak_vel", "peak_acc"):
        if isinstance(out[k], float):
            out[k] *= DEG_PER_U
    for k in ("fSetPosition", "fActPosition"):
        if isinstance(out[k], float):
            out[k] -= A_OFFSET
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
    log("%-38s %.3f s | A set %8.3f deg act %8.3f | error %s | peaks since reset: v %.0f deg/s  a %.0f deg/s^2" % (
        label, dt, a["fSetPosition"], a["fActPosition"], a["bError"], a["peak_vel"], a["peak_acc"]))
    return dt, a


FAILS = []


def expect(label, a, want):
    if not isinstance(a["fSetPosition"], float) or abs(a["fSetPosition"] - want) > 0.05:
        FAILS.append("%s: A %s, expected %.3f" % (label, a["fSetPosition"], want))
        log("  FAIL: A", a["fSetPosition"], "expected", want)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plc", default=tp.PLC_HOST)
    ap.add_argument("--deg-per-u", type=float, default=10.0, help="10: Kin_CAxis /10 setup; 1: A as additional axis")
    ap.add_argument("--a-offset", type=float, default=-600.0,
                    help="axis deg minus G1 A: -600 with Kin_CAxis (SetCoord1 turns the frame's A by 60 u), 0 as additional axis")
    a = ap.parse_args()
    global DEG_PER_U, A_OFFSET
    DEG_PER_U = a.deg_per_u
    A_OFFSET = a.a_offset
    with Plc(a.plc) as p:
        ms = p.sys("GET_MACHINE_STATE")
        if (ms.get("axes_sim_mask", 0) & 7) != 7:
            raise SystemExit("REFUSED: the delta arms are not all simulated")
        to_ready(p)
        log("limits: v %s  a %s  j %s u (1 u = %g deg)" % (tuple(
            num(rpc("read", "%s.%s" % (AX, k))) for k in ("fSWMaxVelocity", "fSWMaxAcceleration", "fSWMaxJerk")) + (DEG_PER_U,)))
        p.m("SetCoord1")
        move(p, "start pose X0 Y0 Z12 A0", X=0.0, Y=0.0, Z=12.0, A=0.0, F=200, ACC=20000, DEA=20000, JERK=80000, Cor=0)
        rpc("write", "GVL.MotionPeakReset", "TRUE")
        rpc("logout")
        for target in (90, 0, 270, -90, 540, -720, 0):
            _, st = move(p, "A -> %d deg (A only)" % target, A=float(target))
            expect("A only %d" % target, st, target)
        _, st = move(p, "X 0->30 mm + A 0->90 (one G1)", X=30.0, A=90.0, F=1000, ACC=100000, DEA=100000, JERK=400000)
        expect("X+A", st, 90)
        _, st = move(p, "X 30->0 mm, A stays", X=0.0, F=1000, ACC=100000, DEA=100000, JERK=400000)
        expect("A sticky", st, 90)
        # queued and blended (Cor 45, as production): each G1 its own A
        seq = [(20.0, 180.0), (-20.0, -45.0), (0.0, 400.0), (10.0, 400.0), (0.0, 30.0)]
        t0 = time.time()
        for x, a_ in seq:
            p.m("G1", X=x, A=a_, F=2000, ACC=200000, DEA=200000, JERK=800000, Cor=45.0)
        p.m("WAIT_FOR_MOTION_STOP", timeout=30, timeout_ms=25000)
        st = axis()
        log("%-38s %.3f s | A set %8.3f deg" % ("queued blended x5 (Cor 45)", time.time() - t0, st["fSetPosition"]))
        expect("queued", st, 30)
        p.m("G1", Cor=0.0, A=0.0)
        p.m("WAIT_FOR_MOTION_STOP", timeout=30, timeout_ms=25000)
        p.sys("GA_EV", ev=8)
        time.sleep(1)
        log("left in", p.sys("GA_EV", ev=0)["st_str"])
        log("RESULT:", "all A positions as commanded" if not FAILS else "FAILED: " + "; ".join(FAILS))


if __name__ == "__main__":
    main()
