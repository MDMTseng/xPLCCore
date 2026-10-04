"""Motion time per part with and without A rotations, on the machine.

    python tools/a_speed_compare.py [--plc 192.168.1.70] [--parts 20] [--label NAME]

Replays the production G1 sequence of one part (pick 3, inspect 6,
place 3, lift; feed 2000 dynamics; Cor 45 so every G1 blends), queues it
whole, waits for the motion to stop and times it -- pure motion time, no
vision waits. Twice: with the A angles production uses (feeder angle,
90 deg at the cameras, a small correction, now and then a 180 deg flip),
and with A held at 0. Seeded, so runs under different axis settings see
the same angles. The delta arms must be virtual; the A axis turns if it
is real.
"""

import argparse
import os
import random
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plc_direct import Plc, Nak  # noqa: E402
import topology as tp  # noqa: E402

SAFE_Z = 12.0
INSP = (15.618, 10.330, 1.1 + 3.4)
SLOT = (41.7, -79.752, -11.4)
DYN = dict(F=2000, ACC=200000, DEA=200000, JERK=800000)   # feedConfig(MOTION.FEED)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def g1(p, **kw):
    while True:
        try:
            return p.m("G1", **kw)
        except Nak:
            time.sleep(0.005)


def part_moves_merged(rng):
    """The same part with every rotation riding on a long arm move: no
    A-only G1. The pick angle on the move to the feeder, 90 deg on the move
    to the cameras, the final angle (flip + correction) on the move toward
    the tape and the approach."""
    pick_a = rng.uniform(-170, 170)
    flip = 180 if rng.random() < 0.3 else 0
    corr = rng.uniform(-8, 8)
    s = rng.randint(0, 2)
    fx, fy = -46.35 + rng.uniform(-15, 15), 30.18 + rng.uniform(-15, 15)
    return [
        dict(X=fx, Y=fy, A=-pick_a),
        dict(Z=2.2),
        dict(Z=SAFE_Z),
        dict(X=INSP[0], Y=INSP[1], Z=INSP[2] + 1, A=90.0),
        dict(Z=INSP[2]),
        dict(Z=INSP[2] + 1),
        dict(X=INSP[0] + (SLOT[0] + 8 - INSP[0]) * 0.5, Y=INSP[1] + (SLOT[1] - INSP[1]) * 0.5, Z=SAFE_Z,
             A=90.0 + flip + corr),
        dict(X=SLOT[0] + 8 * s, Y=SLOT[1], Z=SLOT[2] + 5, A=90.0 + flip + corr),
        dict(Z=SLOT[2]),
        dict(Z=SAFE_Z),
    ]


def part_moves(rng, with_a):
    pick_a = rng.uniform(-170, 170)
    flip = 180 if rng.random() < 0.3 else 0
    corr = rng.uniform(-8, 8)
    s = rng.randint(0, 2)
    fx, fy = -46.35 + rng.uniform(-15, 15), 30.18 + rng.uniform(-15, 15)
    A = (lambda v: {"A": v}) if with_a else (lambda v: {})
    return [
        dict(X=fx, Y=fy, **A(-pick_a)),
        dict(Z=2.2),
        dict(Z=SAFE_Z),
        dict(X=INSP[0], Y=INSP[1], Z=INSP[2] + 1, **A(90.0)),
        dict(Z=INSP[2]),
        dict(**A(90.0)) if with_a else dict(Z=INSP[2]),
        dict(Z=INSP[2] + 1),
        dict(**A(90.0 + flip + corr)) if with_a else dict(Z=INSP[2] + 1),
        dict(X=INSP[0] + (SLOT[0] + 8 - INSP[0]) * 0.5, Y=INSP[1] + (SLOT[1] - INSP[1]) * 0.5, Z=SAFE_Z),
        dict(X=SLOT[0] + 8 * s, Y=SLOT[1], Z=SLOT[2] + 5, **A(90.0 + flip + corr)),
        dict(Z=SLOT[2]),
        dict(Z=SAFE_Z),
    ]


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


def run(p, parts, with_a):
    rng = random.Random(7)
    times = []
    for _ in range(parts):
        moves = part_moves_merged(rng) if with_a == "merged" else part_moves(rng, with_a)
        t0 = time.time()
        for mv in moves:
            g1(p, **mv)
        p.m("WAIT_FOR_MOTION_STOP", timeout=60, timeout_ms=50000)
        times.append(time.time() - t0)
    return times


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plc", default=tp.PLC_HOST)
    ap.add_argument("--parts", type=int, default=20)
    ap.add_argument("--label", default="")
    ap.add_argument("--held-only", action="store_true",
                    help="only the A-held run, never sending A (for a group without the A axis)")
    a = ap.parse_args()
    with Plc(a.plc) as p:
        if (p.sys("GET_MACHINE_STATE").get("axes_sim_mask", 0) & 7) != 7:
            raise SystemExit("REFUSED: the delta arms are not all simulated")
        to_ready(p)
        p.m("SetCoord1")
        start = dict(X=0.0, Y=0.0, Z=SAFE_Z, Cor=45.0, **DYN)     # production start: Cor 45 sticky
        if not a.held_only:
            start["A"] = 0.0
        g1(p, **start)
        p.m("WAIT_FOR_MOTION_STOP", timeout=30, timeout_ms=25000)
        for with_a in ((False,) if a.held_only else (True, "merged", False)):
            t = run(p, a.parts, with_a)
            if not a.held_only:
                g1(p, A=0.0)
            p.m("WAIT_FOR_MOTION_STOP", timeout=30, timeout_ms=25000)
            log("%-28s %-15s motion per part: median %.3f s  mean %.3f s  min %.3f  max %.3f  (%d parts)" % (
                a.label, {True: "A rotating", "merged": "A on long moves", False: "A held at 0"}[with_a],
                statistics.median(t), statistics.mean(t),
                min(t), max(t), len(t)))
        p.sys("GA_EV", ev=8)


if __name__ == "__main__":
    main()
