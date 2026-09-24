"""Virtual-scene regression: run the standard NG scenarios and check them.

    python tools/sim/regression.py [--plc 192.168.1.70] [--parts 10] [--only 0,0p5]

Runs tools/sim/run_virtual.py once per scenario (same seed, so runs are
comparable), saves each under sim_logs/runs/reg_<name>, and checks the
run from its PLC event log and renderer log. Prints a table and exits
non-zero if any check fails. ~1 min per scenario. The plan scenarios run a
production plan, optionally with STOP/RUN at random moments, and check the
tape cell by cell against it (plan_check.py).

Needs what run_virtual.py needs: PLC app running with the delta arms
virtual, CODESYS daemon up, the UI not connected elsewhere.
"""

import argparse
import os
import statistics
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import event_log as E  # noqa: E402
import gantt  # noqa: E402
import plan_check  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
RUNS = os.path.join(REPO, "standalone", "data", "sim_logs", "runs")

# name, label, (side, bottom, tape) NG rates, vision_mock drops
SCENARIOS = [
    ("high", "高 NG 20/15/25%", (0.20, 0.15, 0.25), ""),
    ("5", "各 5%", (0.05, 0.05, 0.05), ""),
    ("0p5", "各 0.5%", (0.005, 0.005, 0.005), ""),
    ("0", "0%", (0.0, 0.0, 0.0), ""),
    # one lost vision reply: the cycle stops (a late reply could be taken for
    # the next part's), cleanly, with the reason shown
    ("drop", "0% + 丟 1 個側面回覆", (0.0, 0.0, 0.0), "114500:5"),
]
SEED = 7

# Production plan under STOP/RUN at random moments: the tape must come out
# cell for cell as planned (plan_check.py). name, label, stops, chaos seed.
PLAN = "4,-5,3,-3,2"
PLAN_NG = 0.1
PLAN_SCENARIOS = [
    ("plan", "計畫 %s，不停" % PLAN, 0, 0),
    ("chaos5", "計畫 + 隨機停 5 次", 5, 11),
    ("chaos8", "計畫 + 隨機停 8 次", 8, 21),
]

# Limits for the virtual scene (mock vision answers in ~40-250 ms).
CYCLE_MEDIAN_MAX_MS = 1400        # place to place, median
TOP_RESULT_SLOW_MS = 700          # plc.md target for the tape path
TOP_RESULT_SLOW_MAX = 1           # allowed outliers per run
SHOT_CLEAR_MM = 30                # arm distance from the tape at a top shot


def run_scenario(name, rates, drops, a):
    cmd = [sys.executable, os.path.join(HERE, "run_virtual.py"), "--plc", a.plc, "--parts", str(a.parts),
           "--ng-side", str(rates[0]), "--ng-btm", str(rates[1]), "--ng-tape", str(rates[2]),
           "--seed", str(SEED), "--save-as", "reg_" + name]
    env = dict(os.environ, VISION_MOCK_DROP=drops)
    p = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    return p.returncode, p.stdout + p.stderr


def run_plan_scenario(name, stops, seed, a):
    cmd = [sys.executable, os.path.join(HERE, "run_virtual.py"), "--plc", a.plc, "--plan=" + PLAN,
           "--ng-side", str(PLAN_NG), "--ng-btm", str(PLAN_NG), "--ng-tape", str(PLAN_NG),
           "--seed", str(SEED), "--save-as", "reg_" + name]
    if stops:
        cmd += ["--chaos", str(stops), "--chaos-seed", str(seed)]
    p = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, p.stdout + p.stderr


def check_plan(name, rc, output):
    results = [("run finished", rc == 0 and "STALL" not in output and "gave up" not in output,
                "exit %d" % rc)]
    try:
        ok, exp, act = plan_check.check(os.path.join(RUNS, "reg_" + name), [int(x) for x in PLAN.split(",")])
        results.append(("tape matches the plan", ok, "expected %s, got %s" % (exp, act)))
    except (OSError, ValueError, IndexError) as e:
        results.append(("logs readable", False, str(e)))
    return results


def check_stop_on_timeout(folder, output, add, results):
    """A lost vision reply must stop the cycle: reason shown, arm lifted."""
    add("stopped with the timeout as reason", "no SideCheckData reply" in output,
        "running state shows the timeout" if "no SideCheckData reply" in output else "no timeout error seen")
    try:
        events = E.load(os.path.join(folder, "events.csv"))
        z = [E.signed(e[3]) for e in events if e[2] == E.POSE_Z]
        add("arm lifted to safe Z after the stop", z and z[-1] >= 11.5, "last Z %.1f" % (z[-1] if z else float("nan")))
    except (OSError, ValueError) as e:
        add("logs readable", False, str(e))
    return results


def check(name, a, rc, output, drops=""):
    folder = os.path.join(RUNS, "reg_" + name)
    results = []

    def add(what, ok, detail):
        results.append((what, bool(ok), detail))

    add("run finished", rc == 0 and "STALL" not in output, "exit %d%s" % (rc, ", STALL" if "STALL" in output else ""))
    if drops:
        return check_stop_on_timeout(folder, output, add, results)
    try:
        d = gantt.build_run(folder)
        events = E.load(os.path.join(folder, "events.csv"))
    except (OSError, ValueError, IndexError) as e:
        add("logs readable", False, str(e))
        return results
    s = d["summary"]
    missing = sum(1 for p in d["tape"]["parts"] if any(st == "missing" for _, st in p["st"]))
    add("packed", s["packed"] == a.parts, "%s of %d" % (s["packed"], a.parts))
    add("places = packed + NG picks", s["places"] == (s["packed"] or 0) + s["ng_picks"],
        "%d places, %d picks" % (s["places"], s["ng_picks"]))
    add("no part missing from the tape", missing == 0, "%d missing" % missing)
    add("reel advances only past OK parts", s["suspicious"] == 0, "%d suspicious" % s["suspicious"])
    dists = E.shot_distances(events)
    near = sum(1 for x in dists if x < SHOT_CLEAR_MM)
    add("top shots clear of the arm", near == 0 and dists,
        "%d of %d under %d mm, min %.0f" % (near, len(dists), SHOT_CLEAR_MM, min(dists) if dists else -1))
    # A place followed by an NG pick from the tape keeps the arm over the
    # tape, and the top shots rightly wait for it to leave: not counted.
    picks = [t for p in d["tape"]["parts"] for t, st in p["st"] if st == "picked"]
    slow = excused = 0
    for r in d["rows"]:
        if r.get("topres") is not None and r["topres"] > TOP_RESULT_SLOW_MS:
            if any(r["t"] < t < r["t"] + r["topres"] for t in picks):
                excused += 1
            else:
                slow += 1
    add("top result within %d ms" % TOP_RESULT_SLOW_MS, slow <= TOP_RESULT_SLOW_MAX,
        "%d slower (allowed %d), %d after an NG pick" % (slow, TOP_RESULT_SLOW_MAX, excused))
    gaps = [r["gap"] for r in d["rows"] if r["gap"] is not None and r["k"] == "place"]
    med = statistics.median(gaps) if gaps else None
    add("cycle median <= %d ms" % CYCLE_MEDIAN_MAX_MS, med is not None and med <= CYCLE_MEDIAN_MAX_MS,
        "%s ms" % (round(med) if med else "-"))
    if drops:
        dropped = sum(1 for line in open(os.path.join(folder, "vision_mock.log"), encoding="utf-8", errors="replace")
                      if "DROPPED push" in line and "104500" not in line)
        timeouts = sum(1 for r in d["rows"] if r["k"] == "toss" and "逾時" in (r["why"] or ""))
        add("each lost reply cost one part", dropped > 0 and timeouts == dropped,
            "%d dropped (side/bottom/top), %d parts sent back" % (dropped, timeouts))
    end_toss = sum(1 for r in d["rows"] if r["k"] == "toss" and ("計畫" in (r["why"] or "") or "plan" in (r["why"] or "")))
    add("no part picked after the plan is done", end_toss == 0, "%d end-of-plan tosses" % end_toss)
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--plc", default="192.168.1.70")
    ap.add_argument("--parts", type=int, default=10)
    ap.add_argument("--only", default=None, help="comma-separated scenario names (high,5,0p5,0,drop,plan,chaos5,chaos8)")
    a = ap.parse_args()

    chosen = [s for s in SCENARIOS if not a.only or s[0] in a.only.split(",")]
    failed = 0
    for name, label, rates, drops in chosen:
        print("== %s (%s)" % (label, name), flush=True)
        rc, out = run_scenario(name, rates, drops, a)
        for what, ok, detail in check(name, a, rc, out, drops):
            failed += 0 if ok else 1
            print("  %s %-40s %s" % ("PASS" if ok else "FAIL", what, detail), flush=True)
    for name, label, stops, seed in PLAN_SCENARIOS:
        if a.only and name not in a.only.split(","):
            continue
        print("== %s (%s)" % (label, name), flush=True)
        rc, out = run_plan_scenario(name, stops, seed, a)
        for what, ok, detail in check_plan(name, rc, out):
            failed += 0 if ok else 1
            print("  %s %-40s %s" % ("PASS" if ok else "FAIL", what, detail), flush=True)
    print("RESULT:", "PASS" if not failed else "FAIL (%d checks)" % failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
