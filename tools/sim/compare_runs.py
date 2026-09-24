"""Compare saved virtual-scene runs side by side, e.g. the real PLC
against the PC soft-PLC sim for the same plan and seed.

    python tools/sim/compare_runs.py gap_53 simgap_53
    python tools/sim/compare_runs.py --pairs gap_5*:simgap_5*

Each run folder is standalone/data/sim_logs/runs/<name> with the run.log
written by run_virtual.py (plan, stops, errors and the timing summary it
prints at the end) plus the tape model that plan_check.py reads.
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
RUNS = os.path.join(REPO, "standalone", "data", "sim_logs", "runs")
sys.path.insert(0, HERE)

STAT = re.compile(r"^\s{2}(\S.*?)\s+n=(\d+)\s+median\s+(-?\d+)\s+p90\s+(-?\d+)"
                  r"\s+max\s+(-?\d+) ms")
STAMP = re.compile(r"^(\d\d:\d\d:\d\d) ")
PLAN = re.compile(r"plan: \{'plan': \[([-\d, ]+)\]\}")


def _t(s):
    return datetime.strptime(s, "%H:%M:%S")


def parse(name):
    path = os.path.join(RUNS, name, "run.log")
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = [l.rstrip("\n") for l in f if "renderer:" not in l]
    r = {"name": name, "stats": {}, "stops": 0, "errors": [], "plan": None,
         "t0": None, "t1": None, "places": None, "advanced": None}
    section = ""
    for l in lines:
        m = STAMP.match(l)
        if m and "run_cycle:" in l and r["t0"] is None:
            r["t0"] = _t(m.group(1))
        if m and ("stopped or error" in l or "event log:" in l) and r["t1"] is None:
            r["t1"] = _t(m.group(1))
        pm = PLAN.search(l)
        if pm:
            r["plan"] = pm.group(1).replace(" ", "")
        if "chaos: STOP" in l:
            r["stops"] += 1
        if "chaos: error" in l or ("err=" in l and "err=None" not in l
                                   and "running=" in l):
            r["errors"].append(l.split(" ", 1)[-1][:90])
        if l.startswith("tape path"):
            section = "tape"
            m2 = re.search(r"(\d+) places", l)
            r["places"] = int(m2.group(1)) if m2 else None
        elif l.startswith("feeder path"):
            section = "feeder"
        m3 = re.search(r"\((\d+) of \d+ places advanced", l)
        if m3:
            r["advanced"] = int(m3.group(1))
        sm = STAT.match(l)
        if sm and section:
            key = "%s: %s" % (section, sm.group(1))
            r["stats"][key] = tuple(int(sm.group(i)) for i in (2, 3, 4, 5))
    if r["t0"] and r["t1"]:
        d = r["t1"] - r["t0"]
        if d < timedelta(0):
            d += timedelta(days=1)
        r["secs"] = int(d.total_seconds())
    else:
        r["secs"] = None
    r["tape"] = tape_result(name, r["plan"])
    return r


def tape_result(name, plan):
    if not plan:
        return None
    try:
        import plan_check
        ok, exp, act = plan_check.check(os.path.join(RUNS, name),
                                        [int(x) for x in plan.split(",")])
        return {"ok": ok, "expected": exp, "actual": act}
    except Exception as ex:  # a run that died early has no tape model
        return {"ok": False, "expected": "?", "actual": "(%s)" % ex}


def fmt(v):
    return "-" if v is None else str(v)


def compare(a, b, out=print):
    ra, rb = parse(a), parse(b)
    out("== %s  vs  %s   (plan %s)" % (a, b, ra["plan"] or "?"))
    if ra["plan"] != rb["plan"]:
        out("   WARNING: different plans: %s / %s" % (ra["plan"], rb["plan"]))
    rows = [
        ("tape result", *[("PASS" if r["tape"] and r["tape"]["ok"] else "FAIL")
                          for r in (ra, rb)]),
        ("errors", *[("; ".join(r["errors"]) or "none") for r in (ra, rb)]),
        ("STOP presses", ra["stops"], rb["stops"]),
        ("run time (s)", ra["secs"], rb["secs"]),
        ("places", ra["places"], rb["places"]),
        ("tape advances", ra["advanced"], rb["advanced"]),
    ]
    w = max(len(k) for k in list(ra["stats"]) + list(rb["stats"]) + ["tape advances"])
    out("   %-*s  %-28s %-28s" % (w, "", a, b))
    for k, va, vb in rows:
        out("   %-*s  %-28s %-28s" % (w, k, fmt(va)[:28], fmt(vb)[:28]))
    out("   %-*s  %-28s %-28s %s" % (w, "timing (median / p90 / max ms)", "", "", "median diff"))
    for k in sorted(set(ra["stats"]) | set(rb["stats"])):
        sa, sb = ra["stats"].get(k), rb["stats"].get(k)
        cell = lambda s: "-" if s is None else "%d / %d / %d  n=%d" % (s[1], s[2], s[3], s[0])
        diff = ""
        if sa and sb and sa[1]:
            diff = "%+d ms (%+.0f%%)" % (sb[1] - sa[1], 100.0 * (sb[1] - sa[1]) / sa[1])
        out("   %-*s  %-28s %-28s %s" % (w, k, cell(sa), cell(sb), diff))
    for r in (ra, rb):
        if r["tape"] and not r["tape"]["ok"]:
            out("   %s tape: expected %s" % (r["name"], r["tape"]["expected"]))
            out("   %s       actual   %s" % (" " * len(r["name"]), r["tape"]["actual"]))
    return ra, rb


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="*", help="two run names: A B")
    ap.add_argument("--pairs", help="GLOB_A:GLOB_B, paired by the trailing number")
    a = ap.parse_args()
    pairs = []
    if a.pairs:
        ga, gb = a.pairs.split(":")
        num = lambda p: re.search(r"(\d+)$", p).group(1)
        la = {num(p): os.path.basename(p) for p in glob.glob(os.path.join(RUNS, ga))}
        lb = {num(p): os.path.basename(p) for p in glob.glob(os.path.join(RUNS, gb))}
        pairs = [(la[k], lb[k]) for k in sorted(set(la) & set(lb), key=int)]
    elif len(a.runs) == 2:
        pairs = [tuple(a.runs)]
    else:
        ap.error("give two run names or --pairs")
    for x, y in pairs:
        compare(x, y)
        print()


if __name__ == "__main__":
    main()
