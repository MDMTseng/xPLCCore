"""Constant-pulse test for one delta drive, for the ASDA stale-target issue
(2026-09-30): EAxis<axis> alone (FSM Powered, group off -- no planner, no
kinematics, no blending) moves up <dist> deg at the slowest joint speed,
0.1 deg/s = ~144.5 increments per 1 ms cycle, then back down. On the ASDA
scope the Cmd Pos then rises 144 or 145 per ms; a stale target shows as a
ms of ~0 followed by ~289. The PLC side is checked at the same time with
the glitch threshold lowered to 50 increments: the Target Position PDO
in the output image (tap) and diSetPosition.

    python tools/pulse_test.py [--axis 0] [--dist 3] [--vel 0.1]

Through the UI's link (standalone UI with XPLC_HARNESS=1). Real drives
move: the owner must be at the machine.
"""

import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "sim"))
sys.argv, _argv = sys.argv[:1], sys.argv
import run_virtual as rv  # noqa: E402
sys.argv = _argv

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RPC = [sys.executable, os.path.join(REPO, "codesys_scripts", "rpc.py")]


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def plc(pkt, timeout_ms=5000):
    return rv.push("plc_send", {"pkt": pkt, "timeoutMs": timeout_ms}, timeout=timeout_ms / 1000 + 5)


def fsm():
    return plc({"type": "SYS", "cmd": "GA_EV", "ev": 0})["st_str"]


def to_powered():
    for _ in range(60):
        st = fsm()
        if st == "Powered":
            return
        ev = {"UnInited": 2, "Error": 8}.get(st, 8)
        try:
            plc({"type": "SYS", "cmd": "GA_EV", "ev": ev})
        except Exception as e:
            log("GA_EV", ev, e)
        time.sleep(0.5)
    raise SystemExit("FSM did not reach Powered (%s)" % fsm())


def thresh(v):
    subprocess.run(RPC + ["write", "GVL.DiGlitchThresh", str(v)], capture_output=True)
    subprocess.run(RPC + ["logout"], capture_output=True)


def stats(label, k):
    e = plc({"type": "SYS", "cmd": "EC_STATS"})
    log("%-10s set-int glitch %s max|d2| %s | tap glitch %s max|d2| %s =now %s =prev %s neither %s | "
        "EasyCAT stale %s skip %s | lost %s" % (
            label, e["dg%d" % k], e["dd%d" % k], e["tg%d" % k], e["td%d" % k], e["tnow%d" % k],
            e["tprev%d" % k], e["tnone%d" % k], e["e_stale"], e["e_skip"], e["lost"]))
    return e


def move(k, dist, vel):
    r = plc({"type": "SYS", "cmd": "JOINT_MOVE", "axis": k, "dist": dist, "vel": vel})
    t0 = time.time()
    log("JOINT_MOVE EAxis%d %+g deg at %g deg/s (%.1f s)" % (k, dist, r.get("vel", vel), abs(dist) / vel))
    end = t0 + abs(dist) / vel + 15
    while time.time() < end:
        time.sleep(5)
        js = plc({"type": "SYS", "cmd": "JOINT_STATE"})
        stats("  %3.0f s" % (time.time() - t0), k)
        if js["s%d" % k] != 1:
            log("  EAxis%d state %s pos %.4f" % (k, js["s%d" % k], js["p%d" % k]))
            return
    plc({"type": "SYS", "cmd": "JOINT_STOP"})
    raise SystemExit("timeout: stopped")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--axis", type=int, default=0)
    ap.add_argument("--dist", type=float, default=3.0)
    ap.add_argument("--vel", type=float, default=0.1)
    ap.add_argument("--thresh", type=int, default=50, help="PLC glitch threshold during the test (increments)")
    a = ap.parse_args()
    to_powered()
    js = plc({"type": "SYS", "cmd": "JOINT_STATE"})
    log("EAxis%d %s at %.4f deg" % (a.axis, "virtual" if js["v%d" % a.axis] else "REAL", js["p%d" % a.axis]))
    thresh(a.thresh)
    try:
        plc({"type": "SYS", "cmd": "EC_STATS", "reset": 1})
        time.sleep(1)
        stats("standstill", a.axis)
        move(a.axis, a.dist, a.vel)
        move(a.axis, -a.dist, a.vel)
        stats("end", a.axis)
    finally:
        thresh(150000)


if __name__ == "__main__":
    main()
