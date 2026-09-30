"""Queued, blended G1s that turn A, over and
over: the move pattern the real PLC hung on right after SM3 4.20 was
installed (2026-09-29, 5 queued G1s at Cor 45 with a different A each).

    python tools/a_blend_stress.py [--plc 127.0.0.1] [--rounds 200] [--seed 1]

Each round queues 3-12 G1s (random X/Y/Z, A, Cor 0/5/45, F up to 2000,
now and then an A-only G1), waits for the stop and checks: the FSM is
still Ready and A ended at the last A sent. Task times (TASK_STATS) are
printed every 20 rounds. The delta arms must be virtual. Close the UI's
PLC link first.
"""

import argparse
import os
import random
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plc_direct import Plc, Nak  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RPC = [sys.executable, os.path.join(REPO, "codesys_scripts", "rpc.py")]


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def rpc(*args):
    r = subprocess.run(RPC + list(args), capture_output=True, text=True)
    return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr.strip()


DEG_PER_U = 10.0     # --deg-per-u: 10 for Kin_CAxis /10, 1 for A as additional axis
A_OFFSET = -600.0    # --a-offset: axis deg minus G1 A (Kin_CAxis in SetCoord1)


def a_set():
    v = rpc("read", "SM_Drive_GenericDSP402.fSetPosition")
    rpc("logout")
    try:
        return float(v.split("#")[-1]) * DEG_PER_U - A_OFFSET
    except ValueError:
        return v


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


def g1(p, **kw):
    while True:
        try:
            return p.m("G1", **kw)
        except Nak:
            time.sleep(0.005)


def task_line(p):
    r = p.sys("TASK_STATS")
    return "  ".join("%s avg %d max %d us" % (r["t%d_name" % i], r["t%d_avg" % i], r["t%d_max" % i])
                     for i in range(r["n"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plc", default="127.0.0.1")
    ap.add_argument("--rounds", type=int, default=200)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--check-every", type=int, default=10, help="read A back every N rounds (slow)")
    ap.add_argument("--deg-per-u", type=float, default=10.0)
    ap.add_argument("--a-offset", type=float, default=-600.0)
    a = ap.parse_args()
    global DEG_PER_U, A_OFFSET
    DEG_PER_U = a.deg_per_u
    A_OFFSET = a.a_offset
    rng = random.Random(a.seed)
    fails = 0
    with Plc(a.plc) as p:
        if (p.sys("GET_MACHINE_STATE").get("axes_sim_mask", 0) & 7) != 7:
            raise SystemExit("REFUSED: the delta arms are not all simulated")
        to_ready(p)
        p.m("SetCoord1")
        g1(p, X=0.0, Y=0.0, Z=12.0, A=0.0, F=200, ACC=20000, DEA=20000, JERK=80000, Cor=0.0)
        p.m("WAIT_FOR_MOTION_STOP", timeout=30, timeout_ms=25000)
        # the exact sequence the real PLC hung on
        seq = [(20.0, 180.0), (-20.0, -45.0), (0.0, 400.0), (10.0, 400.0), (0.0, 30.0)]
        for x, an in seq:
            g1(p, X=x, A=an, F=2000, ACC=200000, DEA=200000, JERK=800000, Cor=45.0)
        p.m("WAIT_FOR_MOTION_STOP", timeout=30, timeout_ms=25000)
        log("the hang sequence: FSM", p.sys("GA_EV", ev=0)["st_str"], "A", a_set())
        t0 = time.time()
        for k in range(1, a.rounds + 1):
            last_a = None
            for _ in range(rng.randint(3, 12)):
                mv = dict(F=rng.choice((300.0, 1000.0, 2000.0)), ACC=200000, DEA=200000, JERK=800000,
                          Cor=rng.choice((0.0, 5.0, 45.0, 45.0)))
                if rng.random() < 0.2:
                    last_a = round(rng.uniform(-720, 720), 1)
                    mv["A"] = last_a
                else:
                    mv.update(X=round(rng.uniform(-50, 50), 2), Y=round(rng.uniform(-50, 50), 2),
                              Z=round(rng.uniform(-10, 12), 2))
                    if rng.random() < 0.7:
                        last_a = round(rng.uniform(-720, 720), 1)
                        mv["A"] = last_a
                g1(p, **mv)
            p.m("WAIT_FOR_MOTION_STOP", timeout=60, timeout_ms=50000)
            st = p.sys("GA_EV", ev=0)
            if st["st_str"] != "Ready":
                fails += 1
                log("round %d: FSM %s %s %s" % (k, st["st_str"], st.get("err_src"), st.get("err_id")))
                to_ready(p)
                p.m("SetCoord1")
                continue
            if k % a.check_every == 0:
                got = a_set()
                ok = last_a is None or (isinstance(got, float) and abs(got - last_a) < 0.05)
                if not ok:
                    fails += 1
                log("round %d: A %s (last sent %s)%s" % (k, got, last_a, "" if ok else "  MISMATCH"))
            if k % 20 == 0:
                log("round %d (%.0f s): %s" % (k, time.time() - t0, task_line(p)))
        g1(p, Cor=0.0, A=0.0)
        p.m("WAIT_FOR_MOTION_STOP", timeout=30, timeout_ms=25000)
        p.sys("GA_EV", ev=8)
    log("RESULT: %d rounds, %d failures" % (a.rounds, fails))


if __name__ == "__main__":
    main()
