"""Gantt timeline of a virtual run, from the PLC event log.

    python tools/sim/gantt.py [--events standalone/data/sim_logs/events.csv]
                              [--ui-log standalone/data/sim_logs/ui.log]
                              [--out standalone/data/sim_logs/gantt.html]

Reads the 1 ms event log that run_virtual.py saves (event_log.py) plus
the renderer log for toss reasons, and writes one self-contained HTML
page: lanes for the arm, nozzle, feeder, the three cameras, the reel and
the per-part result, zoomable, with a per-part table. The page text is
Chinese (it is for the machine's operators).
"""

import argparse
import json
import os
import re
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import event_log as E  # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LOGS = os.path.join(REPO, "standalone", "data", "sim_logs")

REASON_ZH = [
    ("SideCam measure failed", "側面量測 NG"),
    ("SideCheck failed", "側面檢查 NG"),
    ("btm check failed", "底部檢查 NG"),
    ("armOffset is too far", "底部偏移過大"),
    ("slotHoleOffset", "載帶孔偏移過大"),
]


def toss_reasons(ui_log):
    out = []
    try:
        with open(ui_log, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.search(r'checkpoint \[TOSS\] object (\{.*\})', line)
                if not m:
                    continue
                try:
                    reasons = json.loads(m.group(1)).get("tossReasons", [])
                except ValueError:
                    reasons = []
                zh = []
                for r in reasons:
                    for key, label in REASON_ZH:
                        if key in r and label not in zh:
                            zh.append(label)
                out.append("、".join(zh) or "、".join(reasons) or "未知")
    except OSError:
        pass
    return out


def build(events, reasons):
    t0 = events[0][1]
    T = lambda e: e[1] - t0
    lanes = {k: [] for k in ("arm", "nozzle", "feeder", "side", "btm", "reel", "top", "result")}

    # Arm: one bar per motion segment, start -> next start / idle.
    moves = [e for e in events if e[2] in (E.MOVE_START, E.IDLE)]
    waits = []
    mark_zh = {"TOP_RESULT": "頂部結果", "BTM_RESULT": "底部結果", "SIDE_RESULT": "側面結果",
               "FEEDER_RESULT": "柔震盤結果", "VIB_OFF": "停止振動", "PLACE": "放料決定", "TOSS": "丟料決定"}
    for a, b in zip(moves, moves[1:]):
        if a[2] == E.MOVE_START:
            lanes["arm"].append({"s": T(a), "e": T(b), "k": "move", "id": a[3]})
        elif b[1] - a[1] > 1000:
            # Arm idle > 1 s: name the renderer mark that came right before
            # it moved again -- what the cycle was waiting for.
            ends = [e for e in events if a[1] < e[1] <= b[1] and e[2] == E.HOST]
            why = mark_zh.get(E.MARKS.get(ends[0][3], "")) if ends else None
            w = {"s": T(a), "e": T(b), "k": "wait", "why": "等待" + why if why else "期間沒有渲染端標記"}
            lanes["arm"].append(w)
            waits.append(w)

    # Output on/off pairs -> bars.
    on = {}
    for e in events:
        if e[2] == E.OUT_ON:
            on[e[3]] = T(e)
        elif e[2] == E.OUT_OFF and e[3] in on:
            s, bit = on.pop(e[3]), e[3]
            item = {"s": s, "e": T(e), "bit": bit}
            if bit == 0:
                lanes["nozzle"].append(dict(item, k="suck"))
            elif bit == 1:
                lanes["nozzle"].append(dict(item, k="blow"))
            elif bit == 5:
                lanes["feeder"].append(dict(item, k="brake"))
            elif bit == 12:
                lanes["feeder"].append(dict(item, k="shot"))
            elif bit == 8:
                lanes["side"].append(dict(item, k="shot"))
            elif bit == 10:
                lanes["btm"].append(dict(item, k="shot"))
            elif bit == 14:
                lanes["top"].append(dict(item, k="shot"))
            elif bit in (3, 15):
                lanes["top"].append(dict(item, k="light"))

    reel = None
    for e in events:
        if e[2] == E.REEL_START:
            reel = T(e)
        elif e[2] == E.REEL_END and reel is not None:
            lanes["reel"].append({"s": reel, "e": T(e), "k": "reel"})
            reel = None

    vib = light = None
    ti = 0
    results = []
    for e in events:
        if e[2] != E.HOST:
            continue
        code, t = e[3], T(e)
        if code == E.MARK["VIB_ON"]:
            vib = t
        elif code == E.MARK["VIB_OFF"] and vib is not None:
            lanes["feeder"].append({"s": vib, "e": t, "k": "vib"})
            vib = None
        elif code == E.MARK["FEEDER_LIGHT_ON"]:
            light = t
        elif code == E.MARK["FEEDER_LIGHT_OFF"] and light is not None:
            lanes["feeder"].append({"s": light, "e": t, "k": "flight"})
            light = None
        elif code in (E.MARK["TOP_RESULT"], E.MARK["BTM_RESULT"], E.MARK["SIDE_RESULT"], E.MARK["FEEDER_RESULT"]):
            lane = {E.MARK["TOP_RESULT"]: "top", E.MARK["BTM_RESULT"]: "btm",
                    E.MARK["SIDE_RESULT"]: "side", E.MARK["FEEDER_RESULT"]: "feeder"}[code]
            lanes[lane].append({"s": t, "e": t, "k": "result"})
        elif code == E.MARK["PLACE"]:
            results.append({"s": t, "e": t, "k": "place"})
        elif code == E.MARK["TOSS"]:
            results.append({"s": t, "e": t, "k": "toss", "why": reasons[ti] if ti < len(reasons) else "未知"})
            ti += 1
    lanes["result"] = results

    # Per-result rows with the tape path after each place.
    blows = [i for i, e in enumerate(events) if e[2] == E.OUT_ON and e[3] == 1]
    rows = []
    prev = None
    for n, r in enumerate(results):
        row = {"n": n + 1, "t": r["s"], "k": r["k"], "why": r.get("why", ""),
               "gap": None if prev is None else r["s"] - prev}
        if r["k"] == "place":
            # the vacuum break that follows this place mark
            bi = next((i for i in blows if T(events[i]) >= r["s"]), None)
            if bi is not None:
                end = next((i for i in blows if i > bi), None)
                row["reel"] = E._next(events, bi, lambda e: e[2] == E.REEL_START, 1500, end)
                row["shot2"] = E._next(events, bi, lambda e: e[2] == E.OUT_ON and e[3] == 15, 2000, end)
                row["topres"] = E._next(events, bi, lambda e: e[2] == E.HOST and e[3] == E.MARK["TOP_RESULT"], 3000)
        rows.append(row)
        prev = r["s"]

    gaps_place = [r["gap"] for r in rows if r["gap"] is not None and r["k"] == "place"]
    gaps_toss = [r["gap"] for r in rows if r["gap"] is not None and r["k"] == "toss"]
    summary = {
        "span": T(events[-1]),
        "places": sum(1 for r in results if r["k"] == "place"),
        "tosses": sum(1 for r in results if r["k"] == "toss"),
        "gap_place": statistics.median(gaps_place) if gaps_place else None,
        "gap_toss": statistics.median(gaps_toss) if gaps_toss else None,
        "refills": sum(1 for x in lanes["feeder"] if x["k"] == "vib"),
        "shot2": statistics.median([r["shot2"] for r in rows if r.get("shot2") is not None] or [0]),
        "topres": statistics.median([r["topres"] for r in rows if r.get("topres") is not None] or [0]),
        "events": len(events),
        "waits": len(waits),
        "wait_ms": sum(w["e"] - w["s"] for w in waits),
        "wait_why": sorted({w["why"] for w in waits}),
    }
    return {"lanes": lanes, "rows": rows, "summary": summary}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--events", default=os.path.join(LOGS, "events.csv"))
    ap.add_argument("--ui-log", default=os.path.join(LOGS, "ui.log"))
    ap.add_argument("--out", default=os.path.join(LOGS, "gantt.html"))
    ap.add_argument("--packed", type=int, default=None, help="parts the run packed (shown in the header)")
    a = ap.parse_args()

    data = build(E.load(a.events), toss_reasons(a.ui_log))
    data["summary"]["packed"] = a.packed
    tpl = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "gantt_template.html"), encoding="utf-8").read()
    html = tpl.replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False))
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(html)
    print("wrote", a.out, "(%d events, %d results)" % (data["summary"]["events"], len(data["rows"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
