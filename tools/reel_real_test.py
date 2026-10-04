"""Reel axis on the machine: power it, move it, check the cell count and
the odometer against the encoder, and the tracker's stop detection on a
real servo (a virtual axis has no standstill jitter).

    python tools/reel_real_test.py [--plc 192.168.1.70] [--fault]

Only the reel moves: the delta arms must be virtual in the project (the
script checks axes_sim_mask). Close the UI's PLC link first. --fault also
trips the FSM (GVL.TestFaultMidReel = 1, through the CODESYS daemon) while
a tape move runs: the reel must still finish the cell.
"""

import argparse
import os
import statistics
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plc_direct import Plc, Nak  # noqa: E402
import topology as tp  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLOW = {"F": 50, "ACC": 1000, "DEA": 1000, "JERK": 20000}
PROD = {"F": 5000, "ACC": 100000, "DEA": 10000, "JERK": 100000}   # TAPE.REEL_MOVE
CELL = 8.0
TOL_COUNTS = 64          # 0.25 mm: where a real drive may settle after a fast move


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def state(p):
    return p.sys("GA_EV", ev=0)


def to_ready(p):
    last = None
    for _ in range(80):
        r = state(p)
        st = r["st_str"]
        if st != last:
            log("FSM:", st, r.get("err_src") or "")
            last = st
        if st == "Ready":
            return
        ev = {"UnInited": 2, "Powered": 4, "GroupEnabled": 7, "Error": 8}.get(st)
        if ev is not None:
            try:
                p.sys("GA_EV", ev=ev)
            except Nak as e:
                log("GA_EV", ev, "NAK", e)
        time.sleep(0.5)
    raise RuntimeError("FSM did not reach Ready (last %s)" % last)


def odo(p):
    s = p.sys("PLAN_GET")
    return s["reel_odo_counts"], s


def tape(p, cells, kind, dyn, eid):
    t0 = time.time()
    r = p.m("TAPE_CYCLE", timeout=15, motion_id_offset=-1, motion_progress=0,
            Distance=cells * CELL, cells=cells, kind=kind, timeout_ms=10000, ttl_ms=3000,
            tx=1000.0, ty=0.0, tz=0.0, td=1.0, pin_op_seq=[1, 4, 0], event_id=eid, **dyn)
    return r, time.time() - t0


def daemon_write(sym, val):
    subprocess.run([sys.executable, os.path.join(REPO, "codesys_scripts", "rpc.py"), "write", sym, val],
                   check=True, capture_output=True)
    subprocess.run([sys.executable, os.path.join(REPO, "codesys_scripts", "rpc.py"), "logout"],
                   check=False, capture_output=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plc", default=tp.PLC_HOST)
    ap.add_argument("--fault", action="store_true")
    ap.add_argument("--steps", type=int, default=25, help="single production cells for the drift check")
    a = ap.parse_args()
    ok = True
    with Plc(a.plc) as p:
        ms = p.sys("GET_MACHINE_STATE")
        log("axes_sim_mask", ms.get("axes_sim_mask"), "labels", ms.get("axes_labels"))
        if (ms.get("axes_sim_mask", 0) & 7) != 7:
            raise SystemExit("REFUSED: the delta arms are not all simulated")
        to_ready(p)
        ms = p.sys("GET_MACHINE_STATE")
        log("reel after power: axes_err_mask", ms.get("axes_err_mask"), "axes_state", ms.get("axes_state"),
            "counts/mm", ms.get("reel_counts_per_mm"), "counts/turn", ms.get("reel_counts_per_turn"))

        # 1. standstill jitter
        samples = []
        for _ in range(40):
            samples.append(odo(p)[0])
            time.sleep(0.05)
        log("standstill: counts min %d max %d spread %d (%.4f mm)" % (
            min(samples), max(samples), max(samples) - min(samples),
            (max(samples) - min(samples)) / 256.0))

        # 2. slow ReelGo one cell (not counted into a plan; the odometer sees it)
        c0, _ = odo(p)
        p.m("ReelGo", Distance=CELL, **SLOW)
        time.sleep(1.5)
        c1, _ = odo(p)
        log("ReelGo 8 mm slow: odometer %+d counts (%.4f mm), expected -2048" % (c1 - c0, (c1 - c0) / -256.0))
        ok &= abs((c1 - c0) + 2048) <= 4

        # 3. plan + tape cycles
        p.sys("PLAN_SET", seg=[4, -5, 200], plan_id=4242, cells_done=0)
        c0, st0 = odo(p)
        steps = [(1, 1, SLOW, "slow"), (1, 1, PROD, "prod"), (2, 1, PROD, "prod"), (5, 2, PROD, "prod"), (3, 1, PROD, "prod")]
        done = 0
        for k, (cells, kind, dyn, label) in enumerate(steps):
            try:
                r, dt = tape(p, cells, kind, dyn, 900 + k)
            except (Nak, TimeoutError) as e:
                log("TAPE_CYCLE %d cells (%s) FAILED: %s" % (cells, label, e))
                ok = False
                break
            done += cells
            ci = r.get("reel_odo_counts", 0)          # at the reply
            time.sleep(0.8)                           # a real drive creeps the last few counts
            c, st = odo(p)
            log("TAPE_CYCLE %d %s (%s): reply %.3f s reel_ms %s cells_done %s plan_err %s | odometer at reply %+d, settled %+d counts (%.4f mm), expected %d | reel_open %s" % (
                cells, "pack" if kind == 1 else "empty", label, dt, r.get("reel_ms"), r.get("cells_done"),
                r.get("plan_err"), ci - c0, c - c0, (c - c0) / -256.0, -2048 * done, st.get("reel_open")))
            ok &= r.get("cells_done") == done and abs((c - c0) + 2048 * done) <= TOL_COUNTS and not st.get("reel_open")

        # 3b. many single cells at production speed: does the settling error add up?
        devs = []
        for k in range(a.steps):
            r, dt = tape(p, 1, 1 if done < 4 or done >= 9 else 2, PROD, 950 + k)
            done += 1
            time.sleep(0.5)
            c, st = odo(p)
            devs.append((c - c0) + 2048 * done)
        if devs:
            log("%d single prod cells: settled deviation counts first %s last %s min %d max %d (mm %.3f..%.3f)" % (
                len(devs), devs[:5], devs[-5:], min(devs), max(devs), min(devs) / 256.0, max(devs) / 256.0))
            half = len(devs) // 2
            drift = statistics.mean(devs[half:]) - statistics.mean(devs[:half]) if half else 0
            log("drift (mean of 2nd half - 1st half): %.1f counts" % drift)
            ok &= max(abs(d) for d in devs) <= TOL_COUNTS and abs(drift) <= 16

        # 4. an arm fault while the tape moves: the reel must finish the cell
        if a.fault and ok:
            c0, st0 = odo(p)
            d0 = st0["cells_done"]
            daemon_write("GVL.TestFaultMidReel", "1")
            try:
                tape(p, 2, 1, SLOW, 990)
                log("fault test: TAPE_CYCLE finished before the fault fired?")
            except (Nak, TimeoutError) as e:
                log("fault test: TAPE_CYCLE ->", e)
            time.sleep(3.0)
            c, st = odo(p)
            log("fault test: FSM %s | cells_done %s (expected %d) | odometer %+d counts (expected -4096) | reel_open %s moving %s" % (
                state(p)["st_str"], st["cells_done"], d0 + 2, c - c0, st.get("reel_open"), st.get("reel_moving")))
            ok &= st["cells_done"] == d0 + 2 and abs((c - c0) + 4096) <= TOL_COUNTS and not st.get("reel_open")
            to_ready(p)

        p.sys("GA_EV", ev=8)          # reset: power off
        time.sleep(1.0)
        log("left in", state(p)["st_str"])
    log("RESULT:", "PASS" if ok else "FAIL")


if __name__ == "__main__":
    main()
