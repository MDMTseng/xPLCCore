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

# Station setpoints (mm, arm frame), copied from components/CalibPage.tsx.
# The feeder pick area and the side camera are taken from the log instead
# (arm position at suction-on / side-camera trigger).
INSP_LOCATION = (15.618, 10.330)          # bottom camera
SLOT_LOCATION = (41.7, -79.752)           # tape, first slot; slots 8 mm apart in X
SLOT_PITCH = 8.0
REEL_MODULO = 200.0                       # reelpullmotor is a modulo axis (seen wrapping 200 -> 0)
TOSS = [((-61.074, 60.775), "回柔震盤"), ((-31.0, 8.7), "NG 區 1"), ((-63.321, 9.870), "NG 區 2")]
WAIT_FEEDER = (-46.350, 30.181)
PRE_PLACE_FRACTION = 0.5                  # CalibPage: park this far from inspection toward slot 2

REASON_ZH = [
    ("vision timeout: side", "側面視覺逾時"),
    ("vision timeout: bottom", "底部視覺逾時"),
    ("vision timeout: top", "頂部視覺逾時"),
    ("SideCam measure failed", "側面量測 NG"),
    ("SideCheck failed", "側面檢查 NG"),
    ("btm check failed", "底部檢查 NG"),
    ("armOffset is too far", "底部偏移過大"),
    ("slotHoleOffset", "載帶孔偏移過大"),
    # mid-plan: the free slot is past the remaining count (not end-of-plan waste)
    ("production plan: slot", "空格位置超過剩餘數量"),
    ("production plan place count hit", "計畫這段數量已到，丟回"),
    ("production plan is empty", "計畫已完成，丟回"),
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


def top_results(ui_log):
    """Top-camera replies (trig 134500) in order: the three tape slots under
    the camera, slot i at SLOT_LOCATION + i * SLOT_PITCH in X."""
    out, dec = [], json.JSONDecoder()
    try:
        with open(ui_log, encoding="utf-8", errors="replace") as f:
            for line in f:
                i = line.find('{"trig_id":134500')
                if "rx_data" not in line or i < 0:
                    continue
                try:
                    data = dec.raw_decode(line[i:])[0].get("data", {})
                except ValueError:
                    continue
                clear, ok = data.get("is_clear", []), data.get("is_OK", [])
                out.append(["empty" if c else ("ok" if k else "ng") for c, k in zip(clear, ok)])
    except OSError:
        pass
    return out


def ng_picks(ui_log):
    """How often the renderer picked an NG part back out of the tape."""
    try:
        with open(ui_log, encoding="utf-8", errors="replace") as f:
            return sum(1 for line in f if "checkpoint [NG PICK] object" in line)
    except OSError:
        return 0


def build(events, reasons, tops=(), ngpicks=0):
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
            elif bit == 13:
                lanes["feeder"].append(dict(item, k="light"))
            elif bit == 8:
                lanes["side"].append(dict(item, k="shot"))
            elif bit == 9:
                lanes["side"].append(dict(item, k="light"))
            elif bit == 10:
                lanes["btm"].append(dict(item, k="shot"))
            elif bit == 11:
                lanes["btm"].append(dict(item, k="light"))
            elif bit == 14:
                lanes["top"].append(dict(item, k="shot"))
            elif bit in (3, 15):
                # 3 = top side light, 15 = top front light (item["bit"] tells them apart)
                lanes["top"].append(dict(item, k="light"))

    reel = None
    cum = 0.0                              # unwrapped tape travel, mm
    for e in events:
        if e[2] == E.REEL_START:
            reel = (T(e), E.signed(e[3]))
        elif e[2] == E.REEL_END and reel is not None:
            d = E.signed(e[3]) - reel[1]
            if d < -REEL_MODULO / 2:
                d += REEL_MODULO
            lanes["reel"].append({"s": reel[0], "e": T(e), "k": "reel", "u0": round(cum, 2),
                                  "u1": round(cum + d, 2), "cells": round(d / SLOT_PITCH, 2)})
            cum += d
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
    # Slot states from the n-th top result, in order (one reply per mark).
    tops = list(tops)
    for n, r in enumerate(x for x in lanes["top"] if x["k"] == "result"):
        if n < len(tops):
            r["slots"] = tops[n]

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
        "top_ng": sum(1 for x in lanes["top"] if x["k"] == "result" and "ng" in x.get("slots", [])),
        "ng_picks": ngpicks,
    }
    # Arm path: (t ms, x, y, z) from the pose samples.
    pose, cur = [], {}
    for e in events:
        if e[2] in (E.POSE_X, E.POSE_Y, E.POSE_Z):
            cur[e[2]] = E.signed(e[3])
            if e[2] == E.POSE_Z and len(cur) == 3:
                pose.append([T(e), round(cur[E.POSE_X], 1), round(cur[E.POSE_Y], 1), round(cur[E.POSE_Z], 1)])

    def pose_at(t):
        best = None
        for p in pose:
            if p[0] > t + 60:
                break
            best = p
        return best

    picks = [pose_at(x["s"]) for x in lanes["nozzle"] if x["k"] == "suck"]
    picks = [p for p in picks if p and p[2] > 60]      # feeder plate (high Y), not NG re-picks
    sides = [pose_at(x["s"]) for x in lanes["side"] if x["k"] == "shot"]
    sides = [p for p in sides if p]
    med = lambda xs: statistics.median(xs) if xs else None
    top_res = [x for x in lanes["top"] if x["k"] == "result" and "slots" in x]
    for r in results:
        if r["k"] != "place":
            continue
        blow = next((x for x in lanes["nozzle"] if x["k"] == "blow" and x["s"] >= r["s"]), None)
        p = pose_at(blow["s"]) if blow else None
        if p:
            idx = round((p[1] - SLOT_LOCATION[0]) / SLOT_PITCH)
            if 0 <= idx <= 2:
                r["slot"], r["at"] = idx, blow["s"]

    stations = {
        "insp": INSP_LOCATION, "slot": SLOT_LOCATION, "pitch": SLOT_PITCH,
        "toss": [{"x": x, "y": y, "name": n} for (x, y), n in TOSS],
        "wait": WAIT_FEEDER,
        "pre": [INSP_LOCATION[0] + (SLOT_LOCATION[0] + SLOT_PITCH - INSP_LOCATION[0]) * PRE_PLACE_FRACTION,
                INSP_LOCATION[1] + (SLOT_LOCATION[1] - INSP_LOCATION[1]) * PRE_PLACE_FRACTION],
        "picks": [[p[1], p[2]] for p in picks],
        "side": [med([p[1] for p in sides]), med([p[2] for p in sides])] if sides else None,
    }
    tape = tape_model(lanes, pose_at)
    summary["suspicious"] = sum(1 for r in lanes["reel"] if r.get("bad"))
    return {"lanes": lanes, "rows": rows, "summary": summary, "pose": pose, "stations": stations, "tape": tape}


def tape_model(lanes, pose_at):
    """Parts riding on the tape. A part sits at tape position u (mm of tape
    travel); under the camera, slot i is at travel R(t) + i * pitch, so a
    part is drawn at SLOT_LOCATION.x + (u - R(t)) and moves with every reel
    move. Parts appear at a place (slot from the arm X), take their state
    from each top-camera result, vanish when an NG is picked back out of
    the tape (suction on above the tape) or when a result shows their slot
    empty. Every reel move is checked: advancing while the slots that leave
    the camera do not hold an OK part is flagged (bad)."""
    reels = lanes["reel"]

    def R(t):
        u = 0.0
        for r in reels:
            if t >= r["e"]:
                u = r["u1"]
            elif t > r["s"]:
                return r["u0"] + (r["u1"] - r["u0"]) * (t - r["s"]) / max(1, r["e"] - r["s"])
            else:
                break
        return u

    parts = []

    def at(u, t):
        return next((p for p in parts if p["t1"] is None and abs(p["u"] - u) < SLOT_PITCH / 2 and p["t0"] <= t), None)

    events = []
    for r in lanes["result"]:
        if r["k"] == "place" and r.get("slot") is not None:
            events.append((r["at"], "place", r))
    for x in lanes["top"]:
        if x["k"] == "result" and "slots" in x:
            events.append((x["s"], "top", x))
    for x in lanes["nozzle"]:
        if x["k"] == "suck":
            events.append((x["s"], "suck", x))
    for x in reels:
        events.append((x["s"], "reel", x))
    events.sort(key=lambda e: e[0])

    for t, kind, x in events:
        if kind == "place":
            u = R(t) + x["slot"] * SLOT_PITCH
            old = at(u, t)
            if old:
                old["t1"] = t
            parts.append({"u": round(u, 2), "t0": t, "t1": None, "st": [[t, "placed"]]})
        elif kind == "top":
            for i, s in enumerate(x["slots"][:3]):
                u = R(t) + i * SLOT_PITCH
                p = at(u, t)
                if s == "empty":
                    if p:
                        p["st"].append([t, "missing"])
                        p["t1"] = t + 400
                elif p:
                    p["st"].append([t, s])
                else:
                    parts.append({"u": round(u, 2), "t0": t, "t1": None, "st": [[t, s]]})
        elif kind == "suck":
            p0 = pose_at(t)
            if p0 and p0[2] < SLOT_LOCATION[1] + 20:          # suction above the tape: NG pick
                p = at(R(t) + (p0[1] - SLOT_LOCATION[0]), t)
                if p:
                    p["st"].append([t, "picked"])
                    p["t1"] = t + 200
        elif kind == "reel":
            n = max(1, int(round(x["cells"])))
            leaving = []
            for i in range(n):
                p = at(R(t) + i * SLOT_PITCH, t)
                state = p["st"][-1][1] if p else "empty"
                leaving.append(state)
            if any(s != "ok" for s in leaving):
                names = {"empty": "空", "ng": "NG", "placed": "待檢", "missing": "不見", "picked": "已取出"}
                x["bad"] = "前進時第 %s 格是%s" % ("、".join(str(i + 1) for i, s in enumerate(leaving) if s != "ok"),
                                                "、".join(names.get(s, s) for s in leaving if s != "ok"))
    return {"parts": parts}


def packed_count(ui_log):
    """Last packCounter the renderer reported (parts packed)."""
    n = None
    try:
        with open(ui_log, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.search(r'_PACK_INFO_ \{"packCounter":(\d+)\}', line)
                if m:
                    n = int(m.group(1))
    except OSError:
        pass
    return n


def reel_plan_types(ui_log):
    """The renderer's [STEP][REEL ADV] marks with a non-zero advance, in
    order: "pack" or "empty" (a production-plan empty segment)."""
    out = []
    try:
        with open(ui_log, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.search(r'checkpoint \[STEP\]\[REEL ADV\] (\{.*\})', line)
                if m:
                    try:
                        d = json.loads(m.group(1))
                    except ValueError:
                        continue
                    if d.get("adv_count"):
                        out.append(d.get("type"))
    except OSError:
        pass
    return out


def hms(text):
    h, m, s = text.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def wall_offset(folder, lanes):
    """Wall clock (s of day) at run time 0, from vision_mock's bottom-camera
    pushes (1 s resolution) paired in order with the bottom-camera shots.
    Each pair bounds the offset; the reply comes 0-0.4 s after the shot."""
    try:
        with open(os.path.join(folder, "vision_mock.log"), encoding="utf-8", errors="replace") as f:
            walls = [hms(l[:8]) for l in f if " push 124500 " in l]
    except OSError:
        return None
    shots = [it["s"] for it in lanes["btm"] if it["k"] == "shot" and it.get("bit") == 10]
    pairs = list(zip(walls, shots))
    if not pairs:
        return None
    lo = max(w - s / 1000.0 - 0.4 for w, s in pairs)
    hi = min(w + 1 - s / 1000.0 for w, s in pairs)
    if lo <= hi:
        return (lo + hi) / 2
    return statistics.median(w + 0.5 - s / 1000.0 for w, s in pairs)


def chaos_marks(folder, data):
    """STOP / RUN presses from run_virtual's log (run.log), on the run's time
    axis. Snapped to the arm's idle gap they cause when one is close."""
    try:
        with open(os.path.join(folder, "run.log"), encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    off = wall_offset(folder, data["lanes"])
    if off is None:
        return []
    waits = [w for w in data["lanes"]["arm"] if w["k"] == "wait"]
    marks = []
    for l in lines:
        m = re.match(r"(\d\d:\d\d:\d\d) chaos: STOP #(\d+) -> .*'ms': (\d+)\}, plan left (\[.*\])", l)
        if m:
            t = (hms(m.group(1)) + 0.5 - off) * 1000 - int(m.group(3))
            w = next((w for w in waits if abs(w["s"] - t) < 2500), None)
            marks.append({"t": round(w["s"] if w else t), "k": "stop", "n": int(m.group(2)), "left": m.group(4),
                          "label": "停止 #%s（計畫剩 %s）" % (m.group(2), m.group(4))})
            continue
        m = re.match(r"(\d\d:\d\d:\d\d) chaos: RUN again", l)
        if m:
            t = (hms(m.group(1)) + 0.5 - off) * 1000
            w = next((w for w in waits if w["s"] - 500 < t < w["e"] + 1500), None)
            marks.append({"t": round(w["e"] if w else t), "k": "run", "label": "續跑"})
    return marks


def build_run(folder):
    ev, ui = os.path.join(folder, "events.csv"), os.path.join(folder, "ui.log")
    data = build(E.load(ev), toss_reasons(ui), top_results(ui), ng_picks(ui))
    data["summary"]["packed"] = packed_count(ui)
    # A production plan leaves cells empty on purpose: those advances are
    # not suspicious.
    for r, typ in zip(data["lanes"]["reel"], reel_plan_types(ui)):
        r["plan"] = typ
        if typ == "empty":
            r.pop("bad", None)
    data["summary"]["suspicious"] = sum(1 for r in data["lanes"]["reel"] if r.get("bad"))
    data["marks"] = chaos_marks(folder, data)
    data["plan"] = plan_result(folder, data)
    if data["plan"]:
        # the renderer's pack count restarts at every RUN
        data["summary"]["packed"] = data["plan"]["actual"].count("P")
    return data


def plan_result(folder, data):
    """The plan run_virtual set (run.log) against the tape, cell by cell."""
    try:
        with open(os.path.join(folder, "run.log"), encoding="utf-8", errors="replace") as f:
            m = next((re.search(r"plan: \{'plan': (\[[^\]]*\])", l) for l in f if " plan: {" in l), None)
    except OSError:
        return None
    if not m:
        return None
    import plan_check
    plan = json.loads(m.group(1))
    exp, act = plan_check.expected(plan), plan_check.actual(data)
    return {"plan": plan, "expected": exp, "actual": act, "ok": exp == act}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run", action="append", default=[], metavar="LABEL=FOLDER",
                    help="a run to include (folder with events.csv and ui.log, e.g. sim_logs/runs/<name>); "
                         "repeat for several, the page switches between them. Default: sim_logs itself.")
    ap.add_argument("--out", default=os.path.join(LOGS, "gantt.html"))
    a = ap.parse_args()

    specs = a.run or ["最近一次=" + LOGS]
    runs = []
    for spec in specs:
        label, _, folder = spec.partition("=")
        if not os.path.isabs(folder):
            folder = folder if os.path.isdir(folder) else os.path.join(LOGS, folder)
        data = build_run(folder)
        runs.append({"label": label, "data": data})
        print("  %s: %d events, %d results, packed %s" % (label, data["summary"]["events"], len(data["rows"]),
                                                         data["summary"]["packed"]))
    tpl = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "gantt_template.html"), encoding="utf-8").read()
    html = tpl.replace("/*__DATA__*/null", json.dumps(runs, ensure_ascii=False))
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(html)
    print("wrote", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
