"""Slow square on the real delta through the UI's link (standalone UI with
XPLC_HARNESS=1): up to Z0, then the corners of a square (+-HALF mm in X
and Y at Z0), a dip to Z DIP at each corner, back to X0 Y0 Z0. Exact stops
(Cor 0), one G1 at a time, waiting for the motion to stop.

    python tools/square_dip.py [--half 50] [--dip -15] [--f 30] [--acc A] [--jerk J]
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "sim"))
sys.argv, _argv = sys.argv[:1], sys.argv
import run_virtual as rv  # noqa: E402
sys.argv = _argv


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def plc(pkt, timeout_ms=5000):
    return rv.push("plc_send", {"pkt": pkt, "timeoutMs": timeout_ms}, timeout=timeout_ms / 1000 + 5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--half", type=float, default=50.0)
    ap.add_argument("--dip", type=float, default=-15.0)
    ap.add_argument("--f", type=float, default=30.0, help="mm/s")
    ap.add_argument("--continuous", action="store_true",
                    help="queue the whole path at once: blended (Cor --cor) at the corners, "
                         "G4 --dwell at each dip's bottom, one wait at the end")
    ap.add_argument("--cor", type=float, default=7.0, help="corner distance (mm) with --continuous; at most half the dip")
    ap.add_argument("--dwell", type=float, default=0.01, help="s at the dip's bottom with --continuous")
    ap.add_argument("--loops", type=int, default=1, help="laps of the square before going home")
    ap.add_argument("--acc", type=float, help="mm/s^2 (default F*10)")
    ap.add_argument("--jerk", type=float, help="mm/s^3 (default ACC*10)")
    a = ap.parse_args()
    st = plc({"type": "SYS", "cmd": "GA_EV", "ev": 0})["st_str"]
    if st != "Ready":
        raise SystemExit("FSM %s, not Ready" % st)
    acc = a.acc or a.f * 10
    kin = dict(F=a.f, ACC=acc, DEA=acc, JERK=a.jerk or acc * 10, Cor=0.0)
    log("F %g mm/s  ACC %g  JERK %g" % (kin["F"], kin["ACC"], kin["JERK"]))
    h = a.half
    pts = [("up to Z0", None, None, 0.0)]
    for x, y in ((-h, -h), (h, -h), (h, h), (-h, h)) * a.loops:
        pts += [("corner X%+g Y%+g" % (x, y), x, y, 0.0), ("  dip", x, y, a.dip), ("  up", x, y, 0.0)]
    pts.append(("home X0 Y0 Z0", 0.0, 0.0, 0.0))
    if a.continuous:
        pkts = []
        for label, x, y, z in pts:
            # A G1's Cor is the transition from the previous move into it
            # (PLCopen BufferMode): the dip rounds horizontal->down, the next
            # corner up->horizontal; the move up follows the dwell's stop.
            g = dict(kin, Z=z, Cor=0.0 if label in ("  up", "up to Z0") else a.cor)
            if x is not None:
                g.update(X=x, Y=y)
            pkts.append(dict(g, type="M", cmd="G1"))
            if label == "  dip":
                pkts.append({"type": "M", "cmd": "G4", "P": a.dwell})
        t0 = time.time()
        rv.push("plc_send_many", {"pkts": pkts, "timeoutMs": 30000}, timeout=600)
        t1 = time.time()
        plc({"type": "M", "cmd": "WAIT_FOR_MOTION_STOP", "timeout": 60, "timeout_ms": 55000}, 60000)
        log("%d packets" % len(pkts))
        loc = plc({"type": "M", "cmd": "READ_LATEST_CMD_LOCATION"})
        log("queued in %.2f s, done after %.2f s: X %.2f Y %.2f Z %.2f" % (
            t1 - t0, time.time() - t0, loc["X"], loc["Y"], loc["Z"]))
        return
    for label, x, y, z in pts:
        g = dict(kin, Z=z)
        if x is not None:
            g.update(X=x, Y=y)
        plc(dict(g, type="M", cmd="G1"))
        plc({"type": "M", "cmd": "WAIT_FOR_MOTION_STOP", "timeout": 30, "timeout_ms": 25000}, 30000)
        loc = plc({"type": "M", "cmd": "READ_LATEST_CMD_LOCATION"})
        log("%-22s X %7.2f Y %7.2f Z %7.2f" % (label, loc["X"], loc["Y"], loc["Z"]))


if __name__ == "__main__":
    main()
