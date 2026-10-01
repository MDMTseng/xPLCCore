"""Square (or line) paths for the delta through the UI's link (standalone UI
with XPLC_HARNESS=1): up to Z0, then the corners of a square (+-HALF mm in X
and Y at Z0) with a dip to Z DIP at each corner, back to X0 Y0 Z0.

    python tools/square_dip.py [--half 50] [--dip -15] [--f 30] [--acc A] [--jerk J] [--owner-ok]
    python tools/square_dip.py --continuous [--minutes N] [--batch 8] [--path line] [--cor 14] ...

Modes:
- Default: one G1 at a time, exact stops.
- --continuous: the whole path queued and blended.
- --minutes: a background stream (plc_stream_*), with EC_STATS / DEM_STATS
  logged every 10 s.
- --batch N: production-like rounds of N G1s, each run to a stop.

A real delta needs --owner-ok (machine.py).
"""

import argparse
import time

import machine as mc
from machine import log

rv = mc          # rv.push(...) below goes through machine.push
plc = mc.plc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--half", type=float, default=50.0)
    ap.add_argument("--path", choices=("square", "line"), default="square",
                    help="line: back and forth between X -half and X +half at Z0 (no dips), "
                         "the lightest path for the planner")
    ap.add_argument("--dip", type=float, default=-15.0)
    ap.add_argument("--f", type=float, default=30.0, help="mm/s")
    ap.add_argument("--continuous", action="store_true",
                    help="queue the whole path at once: blended (Cor --cor) at the corners, "
                         "G4 --dwell at each dip's bottom, one wait at the end")
    ap.add_argument("--cor", type=float, default=7.0, help="corner distance (mm) with --continuous; at most half the dip")
    ap.add_argument("--dwell", type=float, default=0.01, help="s at the dip's bottom with --continuous")
    ap.add_argument("--minutes", type=float, default=0,
                    help="with --continuous: keep lapping this long (batches of --loops laps, "
                         "sent back to back), EC_STATS logged per batch; Ctrl-C stops after the queue")
    ap.add_argument("--batch", type=int, default=0,
                    help="with --continuous --minutes: production-like rounds -- send this many G1s "
                         "(8 = the UI's MAX_IN_FLIGHT), wait for the motion to stop, repeat")
    ap.add_argument("--loops", type=int, default=1, help="laps of the square before going home")
    ap.add_argument("--acc", type=float, help="mm/s^2 (default F*10)")
    ap.add_argument("--jerk", type=float, help="mm/s^3 (default ACC*10)")
    ap.add_argument("--owner-ok", action="store_true", help="the owner OK'd moving the real delta")
    a = ap.parse_args()
    if not mc.is_delta_virtual():
        mc.require_owner_ok(a.owner_ok)
    st = plc({"type": "SYS", "cmd": "GA_EV", "ev": 0})["st_str"]
    if st != "Ready":
        raise SystemExit("FSM %s, not Ready" % st)
    acc = a.acc or a.f * 10
    kin = dict(F=a.f, ACC=acc, DEA=acc, JERK=a.jerk or acc * 10, Cor=0.0)
    log("F %g mm/s  ACC %g  JERK %g" % (kin["F"], kin["ACC"], kin["JERK"]))
    h = a.half
    pts = [("up to Z0", None, None, 0.0)]
    if a.path == "line":
        for x in (-h, h) * a.loops:
            pts += [("end X%+g" % x, x, 0.0, 0.0)]
    else:
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
        if a.minutes > 0 and a.batch > 0:
            # Rounds of --batch G1s (blended inside a round), each round run
            # to a stop before the next is sent -- like production, where
            # the motion stops between placements.
            lap = [p for p in pkts[1:1 + len(pkts[1:-1]) // a.loops] if p["cmd"] == "G1"]
            rv.push("plc_send_many", {"pkts": pkts[:1]}, timeout=60)
            plc({"type": "M", "cmd": "WAIT_FOR_MOTION_STOP", "timeout": 30, "timeout_ms": 25000}, 30000)
            plc({"type": "SYS", "cmd": "EC_STATS", "reset": 1})
            plc({"type": "SYS", "cmd": "DEM_STATS", "reset": 1})
            k = rounds = 0
            last_log = time.time()
            try:
                while time.time() - t0 < a.minutes * 60:
                    batch = []
                    for j in range(a.batch):
                        g = dict(lap[(k + j) % len(lap)])
                        if j == a.batch - 1:
                            g["Cor"] = 0.0           # the round ends in an exact stop
                        batch.append(g)
                    k += a.batch
                    rv.push("plc_send_many", {"pkts": batch, "timeoutMs": 30000}, timeout=120)
                    plc({"type": "M", "cmd": "WAIT_FOR_MOTION_STOP", "timeout": 30, "timeout_ms": 25000}, 30000)
                    rounds += 1
                    if time.time() - last_log >= 10:
                        last_log = time.time()
                        e = plc({"type": "SYS", "cmd": "EC_STATS", "obj": 1})
                        log("%4.0f s  rounds %4d  lost %d  glitch %s  int-target %s  tap %s =prev %s neither %s | "
                            "EasyCAT stale %s skip %s got-EAxis0 glitch %s | drives 1C32:0B %s" % (
                                time.time() - t0, rounds, e["lost"],
                                "/".join(str(e["g%d" % i]) for i in range(3)),
                                "/".join(str(e["dg%d" % i]) for i in range(3)),
                                "/".join(str(e["tg%d" % i]) for i in range(3)),
                                "/".join(str(e["tprev%d" % i]) for i in range(3)),
                                "/".join(str(e["tnone%d" % i]) for i in range(3)),
                                e["e_stale"], e["e_skip"], e["e_pg"],
                                "/".join(e["o%d" % i].split(",")[7] for i in range(3))))
                        dm = plc({"type": "SYS", "cmd": "DEM_STATS"})
                        log("       drive demand: late %s  stale %s  d2 max %s | lag hist %s" % (
                            "/".join(str(dm["dlate%d" % i]) for i in range(3)),
                            "/".join(str(dm["ds%d" % i]) for i in range(3)),
                            "/".join(str(dm["dmx%d" % i]) for i in range(3)),
                            "  ".join(dm["dl%d" % i].rstrip(",") for i in range(3))))
            except KeyboardInterrupt:
                log("stopping after the current round")
            log("%d rounds of %d G1" % (rounds, a.batch))
            pkts = pkts[-1:]
        elif a.minutes > 0:
            # One background stream for the whole run (the UI keeps 8 in flight,
            # the motion buffer never runs dry); EC_STATS polled alongside
            # every 10 s. Ctrl-C: plc_send_many_abort, the queued moves
            # finish, then home.
            lap = pkts[1:1 + len(pkts[1:-1]) // a.loops]    # one lap
            laps = int(a.minutes * 60 / 1.0) + 1             # over-provision; stopped on time
            plc({"type": "SYS", "cmd": "EC_STATS", "reset": 1})
            plc({"type": "SYS", "cmd": "DEM_STATS", "reset": 1})
            rv.push("plc_stream_start", {"pkts": pkts[:1] + lap * laps, "timeoutMs": 30000})
            try:
                while True:
                    time.sleep(10)
                    st = rv.push("plc_stream_status", {})
                    e = plc({"type": "SYS", "cmd": "EC_STATS", "obj": 1})
                    log("%4.0f s  laps %4d  lost %d tx_err %d rx_err %d  late100 %d  pmax %.0f us  dc_out %d"
                        "  glitch %s  d2max %s%s" % (
                        time.time() - t0, st["sent"] // len(lap), e["lost"], e["tx_err"], e["rx_err"],
                        e["late100"], e["pmax"], e["dc_out"],
                        "/".join(str(e.get("g%d" % k, "?")) for k in range(3)),
                        "/".join("%.3f" % e.get("d2m%d" % k, -1) for k in range(3)),
                        ("  ERR " + st["err"]) if st["err"] else ""))
                    log("       tic_max %.0f us  int-target glitch %s  max|d2| %s | EasyCAT stale %s skip %s"
                        "  arrival %s..%s us (peak %s)  late %s/s (sum %s)" % (
                        e.get("tic_max", -1), "/".join(str(e.get("dg%d" % k)) for k in range(3)),
                        "/".join(str(e.get("dd%d" % k)) for k in range(3)),
                        e.get("e_stale"), e.get("e_skip"), e.get("e_amin"), e.get("e_amax"),
                        e.get("e_apeak"), e.get("e_late"), e.get("e_late_sum")))
                    log("       EasyCAT got EAxis0 target: glitch %s  max|d2| last s %s  peak %s" % (
                        e.get("e_pg"), e.get("e_pd2"), e.get("e_pd2peak")))
                    log("       target PDO tap: glitch %s  max|d2| %s  =now %s  =prev %s  neither %s" % tuple(
                        "/".join(str(e.get("%s%d" % (f, k))) for k in range(3))
                        for f in ("tg", "td", "tnow", "tprev", "tnone")))
                    try:
                        dm = plc({"type": "SYS", "cmd": "DEM_STATS"})
                        log("       drive demand 0x6062 (lag %s): stale %s  late %s  d2 glitch %s max %s  snaps %s | lag hist %s" % (
                            dm.get("lagnom"),
                            "/".join(str(dm.get("ds%d" % k)) for k in range(3)),
                            "/".join(str(dm.get("dlate%d" % k)) for k in range(3)),
                            "/".join(str(dm.get("dgl%d" % k)) for k in range(3)),
                            "/".join(str(dm.get("dmx%d" % k)) for k in range(3)),
                            dm.get("nsnap"), "  ".join(dm.get("dl%d" % k, "").rstrip(",") for k in range(3))))
                    except Exception as ex:
                        log("       drive demand: %s" % ex)
                    if "o0" in e:
                        ob = [e["o%d" % k].rstrip(",").split(",") for k in range(3)]
                        log("       drives 1C32 SM-missed/too-small/sync-err: %s" % "  ".join(
                            "EAxis%d %s/%s/%s" % (k, ob[k][7], ob[k][8], ob[k][9]) for k in range(3)))
                    if not st["running"] or time.time() - t0 > a.minutes * 60:
                        break
            except KeyboardInterrupt:
                pass
            rv.push("plc_send_many_abort", {})
            while rv.push("plc_stream_status", {})["running"]:
                time.sleep(0.5)
            pkts = pkts[-1:]
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
