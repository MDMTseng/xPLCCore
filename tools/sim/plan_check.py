"""Check a run's tape against its production plan, cell by cell.

    python tools/sim/plan_check.py <run folder> "3,-2,4"

The plan is the UI's segment list: positive = pack that many parts,
negative = leave that many cells empty. Expanded it is the exact order
of cells that must leave the top camera: "PPP__PPPP" for 3,-2,4.

The actual order comes from the PLC event log (gantt.tape_model): each
reel move of n cells carries out the n cells at the camera's slots
0..n-1 -- 'P' an OK part, '_' empty, 'X' anything else (NG, unchecked).
Checked: the same string, same length -- a part short, a part too many,
an empty cell too many or in the wrong place all fail.
"""

import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gantt  # noqa: E402


def expected(plan):
    return "".join("P" * n if n > 0 else "_" * (-n) for n in plan)


def actual(run):
    """Cells in the order they left the camera, from the tape model."""
    parts = run["tape"]["parts"]
    pitch = gantt.SLOT_PITCH

    def state(p, t):
        s = None
        for ts, st in p["st"]:
            if ts <= t:
                s = st
        return s

    # A move a fault stopped short and the move that finished it later
    # (REEL_RESUME) are two reel moves of fractions of a cell: count the
    # cell boundaries the tape actually crossed, not moves.
    out = []
    travel = 0.0
    for r in run["lanes"]["reel"]:
        start, travel = travel, travel + r["cells"]
        n = int(math.floor(travel + 0.5)) - int(math.floor(start + 0.5))
        for i in range(n):
            u = r["u0"] + i * pitch
            p = next((p for p in parts if p["t0"] <= r["s"] and (p["t1"] is None or r["s"] < p["t1"])
                      and abs(p["u"] - u) < pitch / 2), None)
            st = state(p, r["s"]) if p else None
            out.append("P" if st == "ok" else "_" if st in (None, "missing", "picked") else "X")
    return "".join(out)


def check(folder, plan):
    run = gantt.build_run(folder)
    exp, act = expected(plan), actual(run)
    return exp == act, exp, act


def main():
    folder, spec = sys.argv[1], sys.argv[2]
    plan = [int(x) for x in spec.split(",")]
    ok, exp, act = check(folder, plan)
    print("expected", exp)
    print("actual  ", act)
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
