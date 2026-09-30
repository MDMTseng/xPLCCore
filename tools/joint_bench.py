"""Delta joint bench steps through the UI's link (the Motors page must be
open in the standalone UI with XPLC_HARNESS=1; its Delta joints panel shows
the same state live, with a STOP).

    python tools/joint_bench.py state
    python tools/joint_bench.py powered              # FSM -> Powered (drives on, group off)
    python tools/joint_bench.py move AXIS DIST [--vel 5] [--owner-ok]
    python tools/joint_bench.py seek AXIS DIST [--vel 5] [--owner-ok]   # stop where the home switch turns on
    python tools/joint_bench.py stop
    python tools/joint_bench.py uninited             # FSM reset (drives off, checked)
    python tools/joint_bench.py real | virtual       # delta real / virtual for this PLC run

Moves are single joints, <= 20 deg/s, |DIST| <= 90, only in Powered (PLC
SYS JOINT_MOVE); on a real delta they need --owner-ok (machine.py). `real`
and `virtual` go through machine.set_delta (GVL.AxisSimConfigMask +
SET_AXIS_SIM, read back); a download or PLC restart brings back the
project's virtual delta.
"""

import argparse
import time

import machine as mc
from machine import log

STATE = ["idle", "moving", "done", "stopped at switch", "error", "no switch in travel", "stopped"]


def show(js=None):
    js = js or mc.sys_cmd("JOINT_STATE")
    for k in range(3):
        log("  EAxis%d %-7s pos %9.3f  switch %-3s  %s%s" % (
            k, "virtual" if js["v%d" % k] else "REAL", js["p%d" % k], "ON" if (js["in"] >> k) & 1 else "off",
            STATE[js["s%d" % k]] if js["s%d" % k] < len(STATE) else js["s%d" % k],
            ("  switch on at %.3f" % js["h%d" % k]) if js["s%d" % k] == 3 else ""))
    return js


def run_move(axis, dist, vel, seek):
    r = mc.sys_cmd("JOINT_MOVE", axis=axis, dist=dist, vel=vel, seek=1 if seek else 0)
    log("JOINT_MOVE EAxis%d %+g deg at %g deg/s%s" % (axis, dist, r.get("vel", vel), " (seek)" if seek else ""))
    end = time.time() + abs(dist) / r.get("vel", vel) + 10
    while time.time() < end:
        if mc.sys_cmd("JOINT_STATE")["s%d" % axis] != 1:
            break
        time.sleep(0.2)
    else:
        mc.sys_cmd("JOINT_STOP")
        log("timeout: stopped")
    time.sleep(0.3)
    show()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=("state", "powered", "uninited", "move", "seek", "stop", "real", "virtual"))
    ap.add_argument("axis", nargs="?", type=int)
    ap.add_argument("dist", nargs="?", type=float)
    ap.add_argument("--vel", type=float, default=5.0)
    ap.add_argument("--owner-ok", action="store_true", help="the owner OK'd moving the real delta")
    a = ap.parse_args()
    if a.what == "state":
        log("FSM", mc.fsm()[0])
        show()
    elif a.what == "stop":
        mc.sys_cmd("JOINT_STOP")
        show()
    elif a.what == "powered":
        mc.fsm_to("Powered")
        log("FSM -> Powered")
        show()
    elif a.what == "uninited":
        mc.drives_off()
        log("FSM -> UnInited, delta powered off")
        show()
    elif a.what in ("move", "seek"):
        if not mc.is_delta_virtual():
            mc.require_owner_ok(a.owner_ok)
        run_move(a.axis, a.dist, a.vel, a.what == "seek")
    elif a.what in ("real", "virtual"):
        mc.set_delta(a.what == "real")
        log("axes_sim_mask", mc.delta_mask())
        show()


if __name__ == "__main__":
    main()
