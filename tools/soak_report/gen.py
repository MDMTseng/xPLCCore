"""Build the soak report page (one self-contained HTML) from a soak log and
its dwell CSV (tools/circle_soak.py --dwell-log). Moved into the repo from a
session scratchpad on 2026-10-06; the owner's standing report format is in
doc/4-dev/claude_memory/soak-report-format.md.

    set SOAK_LOG=tri6h_1005.log  SOAK_CSV=dwell_tri6h.csv  SOAK_PLAN=360
    set SOAK_OUT=tri6h_report.html  SOAK_SUB="triangle 70 %, 6 h"
    python tools/soak_report/gen.py

Logs and CSVs are read from codesys_scripts/jobs/soak_logs/; the page is
written there too (SOAK_OUT).
"""
import csv, json, os, re, sys, time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOGDIR = os.path.join(REPO, "codesys_scripts", "jobs", "soak_logs")
LOG = os.path.join(LOGDIR, os.environ.get("SOAK_LOG", "soak3h_b.log"))
CSV = os.path.join(LOGDIR, os.environ.get("SOAK_CSV", "dwell_3h_b.csv"))
OUT = os.path.join(LOGDIR, os.environ.get("SOAK_OUT", "soak_report.html"))
PLAN_MIN = int(os.environ.get("SOAK_PLAN", "180"))
SUB = os.environ.get("SOAK_SUB", "Square dip 70%、無 filter、每段 60 分鐘共 3 段")

rows, seg, starts, ends, events = [], 0, [], [], []
lost = None
cur = {}
for line in open(LOG, encoding="utf-8", errors="replace"):
    line = line.rstrip()
    m = re.match(r"(\d\d:\d\d:\d\d) === segment (\d+)", line)
    if m:
        seg = int(m.group(2)); events.append((m.group(1), "第 %s 段開始" % seg)); continue
    m = re.match(r"(\d\d:\d\d:\d\d) === DROPOUT (\d+) at ([\d.]+) min", line)
    if m:
        events.append((m.group(1), "⚠ 第 %s 次掉線（soak 第 %s 分鐘），重新下載後繼續" % (m.group(2), m.group(3)))); continue
    m = re.match(r"(\d\d:\d\d:\d\d)\s+([\d.]+) min \| late % ([\d./]+) \| tq max % ([\d./]+) \| >=20 % ([\d/]+) \| >=50 % ([\d/]+) \| (\w+)", line)
    if m:
        cur = {"t": m.group(1), "seg": seg, "min": float(m.group(2)),
               "late": [float(x) for x in m.group(3).split("/")],
               "tq": [float(x) for x in m.group(4).split("/")],
               "ge20": [int(x) for x in m.group(5).split("/")],
               "ge50": [int(x) for x in m.group(6).split("/")], "fsm": m.group(7)}
        rows.append(cur); continue
    m = re.search(r"EtherCAT lost frames (\d+) \| rx errors (\d+) \| tx errors (\d+)", line)
    if m and cur:
        cur["lost"] = int(m.group(1)); cur["rx"] = int(m.group(2)); lost = int(m.group(1)); continue
    m = re.search(r"stops (\d+) \| settled before leaving ([\d.]+) % \| settle <= 15 ms ([\d.]+) % \| err leaving max (\d+) um", line)
    if m and cur:
        cur["stops"] = int(m.group(1)); cur["settled"] = float(m.group(2)); cur["emax"] = int(m.group(4)); continue
    m = re.match(r"(\d\d:\d\d:\d\d) END \((.*?)\)", line)
    if m:
        events.append((m.group(1), "第 %d 段結束：%s" % (seg, {"time": "時間到", "stop file": "手動停止",
                                                          "stream ended early": "串流提前結束"}.get(m.group(2), m.group(2)))))
    m = re.match(r"(\d\d:\d\d:\d\d) === (stopped|all)", line)
    if m:
        events.append((m.group(1), "soak 已停止" if m.group(2) == "stopped" else "全部完成"))

stops = []
if os.path.exists(CSV):
    for r in csv.DictReader(open(CSV)):
        try:
            if int(r["dwell_ms"]) > 1000:      # the pause after homing, not a PnP stop
                continue
            e5 = r.get("err_5ms_um", "")
            e10 = r.get("err_10ms_um", "")
            stops.append([int(r["at_ms"]), int(r["dwell_ms"]), -1 if r["settle_ms"] == "" else int(r["settle_ms"]),
                          int(r["err_stop_um"]), int(r["err_leave_um"]), -1 if e5 in ("", None) else int(e5),
                          -1 if e10 in ("", None) else int(e10)])
        except (KeyError, ValueError):
            pass

# One data point per 15 min of soak time, except that the first point is the
# first minute alone (owner, 2026-10-05: a cold baseline before the 15-min
# windows). Windows: 0-1, 1-15, 15-30, 30-45, ... labelled by their end.
# The PLC's at_ms runs on, except after a re-download (it restarts at 0), so
# count minutes from the first stop and carry an offset over every reset.
TREND_MIN = 15
FIRST_MIN = 1


def window(minute):
    """(lo, hi) of the window a soak minute falls in."""
    if minute < FIRST_MIN:
        return (0, FIRST_MIN)
    k = int(minute // TREND_MIN)
    return (max(FIRST_MIN, k * TREND_MIN), (k + 1) * TREND_MIN)


def soak_minutes(stops):
    """Soak minute of every stop, offsets carried over at_ms resets."""
    out, off, prev, t0 = [], 0, stops[0][0], stops[0][0]
    for s in stops:
        if s[0] < prev - 60000:
            off += prev - t0
            t0 = s[0]
        prev = s[0]
        out.append((off + s[0] - t0) / 60000.0)
    return out


trend = []
if stops:
    buckets = {}
    for s, m in zip(stops, soak_minutes(stops)):
        b = buckets.setdefault(window(m), {"n": 0, "e0": 0, "el": 0, "e5": 0, "n5": 0, "e10": 0, "n10": 0})
        b["n"] += 1; b["e0"] += s[3]; b["el"] += s[4]
        if s[5] >= 0: b["e5"] += s[5]; b["n5"] += 1
        if s[6] >= 0: b["e10"] += s[6]; b["n10"] += 1
    for k in sorted(buckets):
        b = buckets[k]
        trend.append({"m": k[1], "lo": k[0], "hi": k[1], "n": b["n"], "e0": round(b["e0"] / b["n"], 1), "el": round(b["el"] / b["n"], 1),
                      "e5": round(b["e5"] / b["n5"], 1) if b["n5"] else None,
                      "e10": round(b["e10"] / b["n10"], 1) if b["n10"] else None})


def box(vals):
    """min, q1, median, q3, max, mean, p95, n of a list (None when empty)."""
    v = sorted(vals)
    if not v:
        return None
    q = lambda f: v[min(len(v) - 1, int(f * (len(v) - 1) + 0.5))]
    return {"min": v[0], "q1": q(0.25), "med": q(0.5), "q3": q(0.75), "max": v[-1],
            "mean": round(sum(v) / len(v), 1), "p95": q(0.95), "n": len(v)}


# Box plot per 15 min window and over the whole run: error at the stop,
# 5 ms and 10 ms after it, and when leaving.
COLS = {"e0": 3, "el": 4, "e5": 5, "e10": 6}
boxes = []
if stops:
    per = {}
    for s, m in zip(stops, soak_minutes(stops)):
        d = per.setdefault(window(m), {c: [] for c in COLS})
        for c, i in COLS.items():
            if s[i] >= 0:
                d[c].append(s[i])
    for k in sorted(per):
        boxes.append(dict({"m": k[1], "lo": k[0], "hi": k[1]}, **{c: box(per[k][c]) for c in COLS}))
summary = {c: box([s[i] for s in stops if s[i] >= 0]) for c, i in COLS.items()}

def hist(vals, edges):
    h = [0] * (len(edges) + 1)
    for v in vals:
        i = 0
        while i < len(edges) and v >= edges[i]:
            i += 1
        h[i] += 1
    return h

E0 = [0, 5, 10, 15, 20, 25, 30, 40, 50, 100]
EL = [0, 2, 4, 6, 8, 10, 15, 20, 50, 100]
data = {
    "rows": rows, "events": events, "plan": PLAN_MIN, "updated": time.strftime("%Y-%m-%d %H:%M"),
    "nstops": len(stops), "notsettled": sum(1 for s in stops if s[2] < 0),
    "e0": {"edges": E0, "h": hist([s[3] for s in stops], E0)},
    "el": {"edges": EL, "h": hist([s[4] for s in stops], EL)},
    "e5": {"edges": EL, "h": hist([s[5] for s in stops if s[5] >= 0], EL)},
    "e5n": sum(1 for s in stops if s[5] >= 0), "trend": trend, "trend_min": TREND_MIN, "boxes": boxes, "summary": summary,
    "e5max": max([s[5] for s in stops if s[5] >= 0], default=0),
    "e0max": max([s[3] for s in stops], default=0), "elmax": max([s[4] for s in stops], default=0),
    "dwell": sorted(set(s[1] for s in stops))[:6],
    "sub": SUB, "running": not any(e[1] in ("soak 已停止", "全部完成") for e in events),
}
html = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "template.html"), encoding="utf-8").read()
open(OUT, "w", encoding="utf-8").write(html.replace("/*DATA*/null", json.dumps(data, ensure_ascii=False)))
print("rows", len(rows), "stops", len(stops), "->", OUT)
