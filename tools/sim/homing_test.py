"""Homing sequence test on the virtual delta arms (review 2026-09-24 P0-1).

    python tools/sim/homing_test.py [--plc 192.168.1.70] [--switch-pos 10]

Direct TCP to the PLC (the UI must not be connected). Refuses unless
EAxis0/1/2 are virtual. Turns on GVL.SimHomeSwitchEnable so FB_Homing
sees a home switch at --switch-pos on each virtual axis, then runs the
real sequence: EV_RESET, POWER_ON, GROUP_ENABLE, HOME_GO (not
FORCE_SKIP), and checks that a G1 is accepted in Ready. Every FSM state
change is logged with its time. Stall limits: 10 s per power step, 45 s
for homing (its state watchdog is 30 s).
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import queue_test as q  # noqa: E402  (Plc client, daemon rpc)

rpc = q.rpc


def rd(sym):
    return rpc.call({"cmd": "read", "symbol": sym}, 30).get("value")


def wr(sym, val):
    r = rpc.call({"cmd": "write", "symbol": sym, "value": str(val)}, 30)
    if not r.get("ok"):
        raise SystemExit("daemon write %s failed: %s" % (sym, r))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plc", default="192.168.1.70")
    ap.add_argument("--switch-pos", type=float, default=15.0)
    ap.add_argument("--abort-first", action="store_true",
                    help="interrupt a first homing with EV_RESET, then home again")
    a = ap.parse_args()

    for ax in ("EAxis0", "EAxis1", "EAxis2"):
        if str(rd("IoConfig_Globals.%s.bVirtual" % ax)).upper() != "TRUE":
            raise SystemExit("ABORT: %s is not virtual" % ax)
    wr("GVL.SimHomeSwitchPos", a.switch_pos)
    wr("GVL.SimHomeSwitchEnable", "TRUE")
    rpc.call({"cmd": "logout"}, 30)

    plc = q.Plc(a.plc)
    t0 = time.time()
    last = None
    result = 1

    def state():
        r = plc.call({"type": "SYS", "cmd": "GA_EV", "ev": 0}) or {}
        return r.get("st_str"), r.get("err_src")

    def ev(n):
        plc.call({"type": "SYS", "cmd": "GA_EV", "ev": n})

    def wait_state(targets, limit, send=None):
        nonlocal last
        end = time.time() + limit
        while time.time() < end:
            st, err = state()
            if st != last:
                print("  %6.1f s  %-14s %s" % (time.time() - t0, st, err or ""))
                last = st
            if st in targets:
                return st
            if st == "Error":
                return st
            time.sleep(0.2)
        return None

    try:
        # Start from the normal start pose (joints ~ 11 / -37 / 13) rather
        # than wherever an earlier run left the virtual joints.
        ev(8)
        wait_state({"UnInited"}, 10)
        for n, target in ((2, "Powered"), (4, "GroupEnabled"), (7, "Ready")):
            ev(n)
            if wait_state({target}, 10) != target:
                raise SystemExit("could not reach %s for the start pose" % target)
        plc.call({"type": "M", "cmd": "SetCoord1"})
        plc.call({"type": "M", "cmd": "G1", "X": 0, "Y": 0, "Z": 10})
        plc.call({"type": "M", "cmd": "WAIT_FOR_MOTION_STOP", "timeout_ms": 10000}, timeout=12)
        print("  start pose reached")

        if a.abort_first:
            # Interrupted homing must not poison the next attempt (FB_Homing
            # used to keep its expired timer and fail the retry at once).
            ev(8)
            wait_state({"UnInited"}, 10)
            for n, target in ((2, "Powered"), (4, "GroupEnabled"), (6, "Homing")):
                ev(n)
                wait_state({target}, 10)
            time.sleep(0.3)
            print("  interrupting homing with EV_RESET")

        # Power-up. After an interrupted homing the first attempt trips
        # Supervisor:GroupErrorStop (the delta axes were left in errorstop
        # by the power cut; one power cycle clears it), so allow two.
        for attempt in (1, 2):
            ev(8)
            if wait_state({"UnInited"}, 10) != "UnInited":
                raise SystemExit("no UnInited after EV_RESET")
            ev(2)
            st = wait_state({"Powered"}, 10)
            if st == "Powered":
                ev(4)
                st = wait_state({"GroupEnabled"}, 10)
            if st == "GroupEnabled":
                break
            print("  power-up attempt %d ended in %s" % (attempt, st))
        else:
            raise SystemExit("power-up failed twice")
        pos = [rd("IoConfig_Globals.EAxis%d.fActPosition" % i) for i in range(3)]
        rpc.call({"cmd": "logout"}, 30)
        print("  joints before homing:", pos, " switch at", a.switch_pos)
        ev(6)
        st = wait_state({"Ready"}, 45)
        pos = [rd("IoConfig_Globals.EAxis%d.fActPosition" % i) for i in range(3)]
        rpc.call({"cmd": "logout"}, 30)
        print("  joints after homing :", pos)
        if st != "Ready":
            print("RESULT: FAIL (homing ended in %s)" % st)
            return 1
        # Ready's entry holds SMC_GroupReset for 50 scans; give it that.
        time.sleep(0.2)
        r1 = plc.call({"type": "M", "cmd": "SetCoord1"})
        r2 = plc.call({"type": "M", "cmd": "G1", "Z": 10})
        print("  SetCoord1 ->", r1)
        print("  G1 Z10    ->", r2)
        time.sleep(1.0)
        st, err = state()
        ok = bool(r2 and r2.get("ack")) and st == "Ready"
        print("  state after G1: %s %s" % (st, err or ""))
        print("RESULT:", "PASS" if ok else "FAIL")
        result = 0 if ok else 1
    finally:
        plc.close()
        wr("GVL.SimHomeSwitchEnable", "FALSE")
        rpc.call({"cmd": "logout"}, 30)
    return result


if __name__ == "__main__":
    sys.exit(main())
