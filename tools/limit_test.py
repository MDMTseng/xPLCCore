"""Do the axis limits hold when the path asks for far more? Back-and-forth
single-direction moves with absurd G1 dynamics, stop to stop (Cor 0: every
stroke accelerates fully), and the servo peak monitor (GVL.MotionPeak*)
against each axis' configured limits.

    python tools/limit_test.py [--plc 192.168.1.70] [--strokes 6]

The delta arms must be virtual (they are commanded to their limits); the
A axis turns if it is real. Close the UI's PLC link first.
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
AXES = [("EAxis0", "IoConfig_Globals.EAxis0"), ("EAxis1", "IoConfig_Globals.EAxis1"),
        ("EAxis2", "IoConfig_Globals.EAxis2"), ("A", "SM_Drive_GenericDSP402")]
HUGE = dict(F=100000, ACC=10000000, DEA=10000000, JERK=1000000000, Cor=0)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def rpc(*args):
    r = subprocess.run(RPC + list(args), capture_output=True, text=True)
    return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr.strip()


def num(s):
    try:
        return float(s.split("#")[-1])
    except ValueError:
        return s


def peaks():
    out = [[num(rpc("read", "GVL.MotionPeak%s[%d]" % (k, i))) for k in ("Vel", "Acc", "Jerk")] for i in range(4)]
    rpc("logout")
    return out


def limits():
    out = [[num(rpc("read", "%s.%s" % (sym, k))) for k in ("fSWMaxVelocity", "fSWMaxAcceleration", "fSWMaxJerk")]
           for _, sym in AXES]
    rpc("logout")
    return out


def to_ready(p):
    for _ in range(80):
        st = p.sys("GA_EV", ev=0)["st_str"]
        if st == "Ready":
            return
        ev = {"UnInited": 2, "Powered": 4, "GroupEnabled": 7, "Error": 8}.get(st)
        if ev is not None:
            try:
                p.sys("GA_EV", ev=ev)
            except Nak:
                pass
        time.sleep(0.5)
    raise RuntimeError("FSM did not reach Ready")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plc", default="192.168.1.70")
    ap.add_argument("--strokes", type=int, default=6)
    a = ap.parse_args()
    cases = [
        ("X -50 <-> +50", dict(X=-50.0), dict(X=50.0)),
        ("Y -50 <-> +50", dict(Y=-50.0), dict(Y=50.0)),
        ("Z +12 <-> -15", dict(Z=12.0), dict(Z=-15.0)),
        ("XY diagonal", dict(X=-40.0, Y=-40.0), dict(X=40.0, Y=40.0)),
        ("A 0 <-> 360 (A only)", dict(A=0.0), dict(A=360.0)),
        ("X -50 <-> +50 with A 0 <-> 180", dict(X=-50.0, A=0.0), dict(X=50.0, A=180.0)),
    ]
    with Plc(a.plc) as p:
        if (p.sys("GET_MACHINE_STATE").get("axes_sim_mask", 0) & 7) != 7:
            raise SystemExit("REFUSED: the delta arms are not all simulated")
        to_ready(p)
        lim = limits()
        log("limits (v, a, j):", ", ".join("%s %s" % (n, l) for (n, _), l in zip(AXES, lim)))
        p.m("SetCoord1")
        p.m("G1", X=0.0, Y=0.0, Z=12.0, A=0.0, F=200, ACC=20000, DEA=20000, JERK=80000, Cor=0)
        p.m("WAIT_FOR_MOTION_STOP", timeout=30, timeout_ms=25000)
        worst = True
        for name, pa, pb in cases:
            p.m("G1", **dict(pa, F=200, ACC=20000, DEA=20000, JERK=80000, Cor=0))
            p.m("WAIT_FOR_MOTION_STOP", timeout=30, timeout_ms=25000)
            rpc("write", "GVL.MotionPeakReset", "TRUE")
            rpc("logout")
            t0 = time.time()
            for k in range(a.strokes):
                p.m("G1", **dict(pb if k % 2 == 0 else pa, **HUGE))
            p.m("WAIT_FOR_MOTION_STOP", timeout=60, timeout_ms=50000)
            dt = (time.time() - t0) / a.strokes
            pk = peaks()
            st = p.sys("GA_EV", ev=0)
            parts = []
            for (n, _), (v, ac, j), (lv, la, lj) in zip(AXES, pk, lim):
                ok = v <= lv * 1.01 and ac <= la * 1.02
                worst &= ok
                parts.append("%s v %.0f/%.0f a %.0f/%.0f j %.2g/%.2g%s" % (n, v, lv, ac, la, j, lj, "" if ok else " OVER"))
            log("%-32s %.3f s/stroke | FSM %s | %s" % (name, dt, st["st_str"], " | ".join(parts)))
            if st["st_str"] != "Ready":
                log("  error source:", st.get("err_src"), st.get("err_id"))
                to_ready(p)
                p.m("SetCoord1")
        p.sys("GA_EV", ev=8)
        log("RESULT:", "velocities and accelerations within limits" if worst else "a limit was exceeded")


if __name__ == "__main__":
    main()
