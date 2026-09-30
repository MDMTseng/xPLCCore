"""Delta joint bench steps through the UI's link (the Motors page must be
open in the standalone UI with XPLC_HARNESS=1; its Delta joints panel shows
the same state live, with a STOP).

    python tools/joint_bench.py state
    python tools/joint_bench.py powered              # FSM -> Powered (drives on, group off)
    python tools/joint_bench.py move AXIS DIST [--vel 5]
    python tools/joint_bench.py seek AXIS DIST [--vel 5]   # stop where the home switch turns on
    python tools/joint_bench.py stop
    python tools/joint_bench.py uninited             # FSM reset (drives off)
    python tools/joint_bench.py real | virtual       # delta real / virtual (FSM UnInited)

Moves are single joints, <= 20 deg/s, |DIST| <= 90, only in Powered (PLC
SYS JOINT_MOVE). `real` clears the project's virtual mask for this run of
the PLC (GVL.AxisSimConfigMask) and applies SET_AXIS_SIM 0; a PLC restart
brings back the project's virtual delta.
"""

import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "sim"))
sys.argv, _argv = sys.argv[:1], sys.argv     # run_virtual parses nothing at import
import run_virtual as rv  # noqa: E402
sys.argv = _argv

STATE = ["idle", "moving", "done", "stopped at switch", "error", "no switch in travel", "stopped"]
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RPC = [sys.executable, os.path.join(REPO, "codesys_scripts", "rpc.py")]


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def plc(pkt, timeout_ms=5000):
    return rv.push("plc_send", {"pkt": pkt, "timeoutMs": timeout_ms}, timeout=timeout_ms / 1000 + 5)


def fsm():
    return plc({"type": "SYS", "cmd": "GA_EV", "ev": 0})["st_str"]


def show(js=None):
    js = js or plc({"type": "SYS", "cmd": "JOINT_STATE"})
    for k in range(3):
        log("  EAxis%d %-7s pos %9.3f  switch %-3s  %s%s" % (
            k, "virtual" if js["v%d" % k] else "REAL", js["p%d" % k], "ON" if (js["in"] >> k) & 1 else "off",
            STATE[js["s%d" % k]] if js["s%d" % k] < len(STATE) else js["s%d" % k],
            ("  switch on at %.3f" % js["h%d" % k]) if js["s%d" % k] == 3 else ""))
    return js


def go_state(want):
    for _ in range(100):
        st = fsm()
        if st == want:
            return st
        ev = 8 if want == "UnInited" else {"UnInited": 2, "Error": 8}.get(st)
        if want == "Powered" and st not in ("UnInited", "Error", "Powering", "Powered"):
            ev = 8
        if ev is not None:
            try:
                plc({"type": "SYS", "cmd": "GA_EV", "ev": ev})
            except Exception as e:
                log("GA_EV", ev, e)
        time.sleep(0.3)
    raise SystemExit("FSM did not reach %s (%s)" % (want, fsm()))


def run_move(axis, dist, vel, seek):
    r = plc({"type": "SYS", "cmd": "JOINT_MOVE", "axis": axis, "dist": dist, "vel": vel, "seek": 1 if seek else 0})
    log("JOINT_MOVE EAxis%d %+g deg at %g deg/s%s" % (axis, dist, r.get("vel", vel), " (seek)" if seek else ""))
    end = time.time() + abs(dist) / r.get("vel", vel) + 10
    while time.time() < end:
        js = plc({"type": "SYS", "cmd": "JOINT_STATE"})
        if js["s%d" % axis] != 1:
            break
        time.sleep(0.2)
    else:
        plc({"type": "SYS", "cmd": "JOINT_STOP"})
        log("timeout: stopped")
    time.sleep(0.3)
    show()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=("state", "powered", "uninited", "move", "seek", "stop", "real", "virtual"))
    ap.add_argument("axis", nargs="?", type=int)
    ap.add_argument("dist", nargs="?", type=float)
    ap.add_argument("--vel", type=float, default=5.0)
    a = ap.parse_args()
    if a.what == "state":
        log("FSM", fsm()); show()
    elif a.what == "stop":
        plc({"type": "SYS", "cmd": "JOINT_STOP"}); show()
    elif a.what == "powered":
        log("FSM ->", go_state("Powered")); show()
    elif a.what == "uninited":
        log("FSM ->", go_state("UnInited")); show()
    elif a.what in ("move", "seek"):
        run_move(a.axis, a.dist, a.vel, a.what == "seek")
    elif a.what in ("real", "virtual"):
        go_state("UnInited")
        # GVL.AxisSimConfigMask is re-applied on every UnInited entry, so it
        # must follow too (virtual once only sent SET_AXIS_SIM 7, and the
        # next FSM reset made the delta real again -- 2026-09-30).
        subprocess.run(RPC + ["write", "GVL.AxisSimConfigMask", "0" if a.what == "real" else "7"],
                       capture_output=True)
        subprocess.run(RPC + ["logout"], capture_output=True)
        r = plc({"type": "SYS", "cmd": "SET_AXIS_SIM", "mask": 0 if a.what == "real" else 7})
        log("SET_AXIS_SIM ->", r.get("mask"))
        want = 0 if a.what == "real" else 7
        for _ in range(60):
            ms = plc({"type": "SYS", "cmd": "GET_MACHINE_STATE"})
            if (ms.get("axes_sim_mask", -1) & 7) == want:
                break
            time.sleep(0.5)
        log("axes_sim_mask", ms.get("axes_sim_mask"))
        show()


if __name__ == "__main__":
    main()
