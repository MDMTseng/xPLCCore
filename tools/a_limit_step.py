"""One step of the A limit ladder on the machine (the Motors page's test
from the command line, for a session where the operator reports the mark).

    python tools/a_limit_step.py --factor 1.5 [--mode time|all] [--swing 180] [--cycles 20]
    python tools/a_limit_step.py --restore

Sets A's run-time limits to the downloaded ones scaled by --factor
(time: v x f, a x f^2, j x f^3), brings the FSM to Ready, swings A back and
forth from and back to 0 (only A moves; the arm holds its TCP), and leaves
A at 0 with the FSM Ready (holding torque) for the operator to check the
mark. --restore sets the downloaded limits back (FSM left in UnInited).
Close the UI's PLC link first.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plc_direct import Plc, Nak  # noqa: E402

A = 3
DEG_PER_U = 10.0


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def state(p):
    return p.sys("GA_EV", ev=0)["st_str"]


def to_state(p, want):
    for _ in range(100):
        st = state(p)
        if st == want:
            return
        if want == "UnInited":
            ev = 8
        else:
            ev = {"UnInited": 2, "Powered": 4, "GroupEnabled": 7, "Error": 8}.get(st)
        if ev is not None:
            try:
                p.sys("GA_EV", ev=ev)
            except Nak:
                pass
        time.sleep(0.3)
    raise RuntimeError("FSM did not reach %s" % want)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plc", default="192.168.1.70")
    ap.add_argument("--factor", type=float, default=1.0)
    ap.add_argument("--mode", choices=("time", "all"), default="time")
    ap.add_argument("--swing", type=float, default=180.0)
    ap.add_argument("--cycles", type=int, default=20)
    ap.add_argument("--restore", action="store_true")
    a = ap.parse_args()
    with Plc(a.plc) as p:
        if a.restore:
            to_state(p, "UnInited")
            r = p.sys("SET_AXIS_LIMITS", axis=A, restore=1)
            log("A limits restored (u): v %.0f a %.0f j %.0f" % (r["lv"], r["la"], r["lj"]))
            return
        i = p.sys("AXIS_INFO", axis=A)
        f = a.factor
        if a.mode == "time":
            v, acc, j = i["cv"] * f, i["ca"] * f * f, i["cj"] * f ** 3
        else:
            v, acc, j = i["cv"] * f, i["ca"] * f, i["cj"] * f
        to_state(p, "UnInited")
        p.sys("SET_AXIS_LIMITS", axis=A, v=v, a=acc, d=acc, j=j)
        log("x%g (%s): A limits v %.0f deg/s (%.0f rpm)  a %.0f deg/s^2  j %.0f deg/s^3" % (
            f, a.mode, v * DEG_PER_U, v * DEG_PER_U / 6, acc * DEG_PER_U, j * DEG_PER_U))
        to_state(p, "Ready")
        i = p.sys("AXIS_INFO", axis=A)
        if not i.get("arm_ok"):
            raise SystemExit("arm position not valid: refused")
        p.m("G1", X=i["arm_x"], Y=i["arm_y"], Z=i["arm_z"], A=0.0, F=50, ACC=2000, DEA=2000, JERK=20000, Cor=0.0)
        p.m("WAIT_FOR_MOTION_STOP", timeout=35, timeout_ms=30000)
        p.sys("AXIS_INFO", axis=A, reset_peaks=1)
        time.sleep(0.05)
        fast = dict(F=100000, ACC=10000000, DEA=10000000, JERK=1000000000, Cor=0.0)
        t0 = time.time()
        for _ in range(a.cycles):
            p.m("G1", A=a.swing, timeout=60, **fast)
            p.m("G1", A=0.0, timeout=60, **fast)
        p.m("WAIT_FOR_MOTION_STOP", timeout=125, timeout_ms=120000)
        dt = time.time() - t0
        i = p.sys("AXIS_INFO", axis=A)
        n = 2 * a.cycles
        log("%d moves of %g deg in %.2f s = %.3f s each; peaks v %.0f%% a %.0f%% of the limits; FSM %s" % (
            n, a.swing, dt, dt / n, 100 * i["pv"] / i["lv"], 100 * i["pa"] / i["la"], state(p)))
        log("A is at 0 and held: check the mark")


if __name__ == "__main__":
    main()
