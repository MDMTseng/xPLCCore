"""PLC event timestamp log: collect and analyse the timing-critical paths.

    python tools/sim/event_log.py collect [--plc 192.168.1.70] [--seconds 60] [--out events.csv]
    python tools/sim/event_log.py analyze events.csv

The PLC keeps a 1 ms event ring (GVL.EvHead, written by PRG_EventLog and
by SYS EVT_MARK) and serves it at GET /e?s=<seq> on :8126 as lines
"seq t_ms kind val", first line "head <EvHead>". Works while the UI owns
the PLC's TCP port; run_virtual.py runs a collector during its run.

Kinds: 1/2 output on/off (val = bit), 3/4 input on/off, 5 motion segment
start (val = movement id), 6 group idle, 7/8 reel move start/end,
9 FSM state, 10 host mark (val = MARKS code, sent by CalibPage.tsx),
11/12/13 arm X/Y/Z (0.01 mm, every 50 ms while moving; see signed()).

The two paths (doc/3-subsystems/plc.md, "Timing-critical paths"):
  tape   : from the vacuum-break pulse after placing (Nozzle_blow on) to
           reel start/stop, top shot 1 (side light), top shot 2 (light 0)
           and the top-camera result. Target < 0.7 s to the result.
  feeder : from vibration on (host mark) to brake, vibration off, feeder
           light, feeder camera and its result. Target ~1 s.
"""

import argparse
import csv
import statistics
import sys
import threading
import time
import urllib.request

OUT_NAMES = {0: "Nozzle_suck", 1: "Nozzle_blow", 3: "CAM_Top_SideLight", 5: "FlexVib_brake",
             6: "ReelAdv", 7: "ReelWheelFeed", 8: "CAM_Side", 9: "CAM_Side_Light0",
             10: "CAM_Btm", 11: "CAM_Btm_Light0", 12: "CAM_FlexFeeder", 13: "CAM_FlexFeeder_Light0",
             14: "CAM_Top", 15: "CAM_Top_Light0"}
# CalibPage.tsx EVT
MARKS = {1: "TOP_RESULT", 2: "BTM_RESULT", 3: "SIDE_RESULT", 4: "FEEDER_RESULT",
         5: "VIB_ON", 6: "VIB_OFF", 7: "FEEDER_LIGHT_ON", 8: "FEEDER_LIGHT_OFF",
         9: "PLACE", 10: "TOSS"}
MARK = {v: k for k, v in MARKS.items()}

OUT_ON, OUT_OFF, IN_ON, IN_OFF, MOVE_START, IDLE, REEL_START, REEL_END, FSM, HOST, POSE_X, POSE_Y, POSE_Z = range(1, 14)


def signed(v):
    """Pose values are DINT bit patterns in 0.01 mm."""
    return (v - (1 << 32) if v >= (1 << 31) else v) / 100.0


class Collector:
    """Polls GET /e and keeps every event, in order. Shares the PLC's
    one-connection HTTP server with vision_mock.py; keep the rate low."""

    def __init__(self, plc, period=0.3):   # 1024-event ring: minutes of headroom
        self.url = "http://%s:8126/e?s=%%d" % plc
        self.period = period
        self.events = []          # (seq, t_ms, kind, val)
        self.next = None          # next seq to ask for
        self.lost = 0
        self.errors = 0
        self._stop = threading.Event()
        self._thread = None

    def poll_once(self):
        start = 0 if self.next is None else self.next
        # >1 s: a busy one-connection server costs one SYN re-send (~1 s).
        with urllib.request.urlopen(self.url % start, timeout=2.5) as r:
            lines = r.read().decode("ascii", "replace").split("\n")
        head = int(lines[0].split()[1])
        if self.next is None:
            # First poll only fixes the starting point: record from now on.
            self.next = head
            return
        for line in lines[1:]:
            parts = line.split()
            if len(parts) != 4:
                continue
            seq, t, k, v = (int(x) for x in parts)
            if seq < self.next:
                continue
            if seq > self.next:
                self.lost += seq - self.next
            self.events.append((seq, t, k, v))
            self.next = seq + 1

    def run(self):
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                self.errors += 1
            self._stop.wait(self.period)

    def start(self):
        self._thread = threading.Thread(target=self.run, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        try:
            self.poll_once()          # drain what is left
        except Exception:
            pass

    def save(self, path):
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["seq", "t_ms", "kind", "val"])
            w.writerows(self.events)


def load(path):
    with open(path, newline="") as f:
        return [tuple(int(x) for x in row) for i, row in enumerate(csv.reader(f)) if i > 0]


def _next(events, i, pred, t_limit, end=None):
    """First event after index i matching pred, within t_limit ms of
    events[i] and before index end (the next anchor, so a cycle never
    picks up the following cycle's events)."""
    t0 = events[i][1]
    for j in range(i + 1, len(events) if end is None else end):
        if events[j][1] - t0 > t_limit:
            return None
        if pred(events[j]):
            return events[j][1] - t0
    return None


def _stats(name, xs, target=None):
    xs = [x for x in xs if x is not None]
    if not xs:
        return "  %-26s   (none)" % name
    xs.sort()
    p90 = xs[min(len(xs) - 1, int(round(0.9 * (len(xs) - 1))))]
    s = "  %-26s n=%-3d median %5d  p90 %5d  max %5d ms" % (name, len(xs), statistics.median(xs), p90, xs[-1])
    if target is not None:
        over = sum(1 for x in xs if x > target)
        s += "   > %d ms: %d" % (target, over)
    return s


def analyze(events, out=print):
    is_out_on = lambda bit: (lambda e: e[2] == OUT_ON and e[3] == bit)
    is_mark = lambda code: (lambda e: e[2] == HOST and e[3] == code)

    anchors = [i for i, e in enumerate(events) if e[2] == OUT_ON and e[3] == 1]
    out("tape path, from the vacuum break after placing (Nozzle_blow on), %d places:" % len(anchors))
    cols = {"reel start": [], "reel stop": [], "top shot 1": [], "top shot 2": [], "top result": [],
            "shot 2 -> result (vision)": []}
    for n, i in enumerate(anchors):
        end = anchors[n + 1] if n + 1 < len(anchors) else None
        cols["reel start"].append(_next(events, i, lambda e: e[2] == REEL_START, 1500, end))
        cols["reel stop"].append(_next(events, i, lambda e: e[2] == REEL_END, 2000, end))
        cols["top shot 1"].append(_next(events, i, is_out_on(14), 2000, end))
        shot2 = _next(events, i, is_out_on(15), 2000, end)
        res = _next(events, i, is_mark(MARK["TOP_RESULT"]), 3000)
        cols["top shot 2"].append(shot2)
        cols["top result"].append(res)
        cols["shot 2 -> result (vision)"].append(res - shot2 if res is not None and shot2 is not None else None)
    out("  (%d of %d places advanced the tape)" % (sum(1 for x in cols["reel start"] if x is not None), len(anchors)))
    for k, v in cols.items():
        out(_stats(k, v, 700 if k == "top result" else None))
    gaps = [events[b][1] - events[a][1] for a, b in zip(anchors, anchors[1:])]
    out(_stats("place to next place", gaps))

    vib = [i for i, e in enumerate(events) if e[2] == HOST and e[3] == MARK["VIB_ON"]]
    out("feeder path, from vibration on, %d refills:" % len(vib))
    cols = {"brake on": [], "vibration off": [], "feeder light on": [], "feeder camera": [], "feeder result": []}
    for i in vib:
        cols["brake on"].append(_next(events, i, is_out_on(5), 3000))
        cols["vibration off"].append(_next(events, i, is_mark(MARK["VIB_OFF"]), 3000))
        cols["feeder light on"].append(_next(events, i, is_mark(MARK["FEEDER_LIGHT_ON"]), 3000))
        cols["feeder camera"].append(_next(events, i, is_out_on(12), 3000))
        cols["feeder result"].append(_next(events, i, is_mark(MARK["FEEDER_RESULT"]), 4000))
    for k, v in cols.items():
        out(_stats(k, v, 1000 if k == "feeder result" else None))
    shots = [i for i, e in enumerate(events) if e[2] == OUT_ON and e[3] == 12]
    out(_stats("feeder camera to result", [_next(events, i, is_mark(MARK["FEEDER_RESULT"]), 3000) for i in shots]))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--plc", default="192.168.1.70")
    c.add_argument("--seconds", type=float, default=60)
    c.add_argument("--out", default="events.csv")
    a = sub.add_parser("analyze")
    a.add_argument("file")
    args = ap.parse_args()

    if args.cmd == "collect":
        col = Collector(args.plc).start()
        time.sleep(args.seconds)
        col.stop()
        col.save(args.out)
        print("%d events -> %s (lost %d, poll errors %d)" % (len(col.events), args.out, col.lost, col.errors))
        analyze(col.events)
    else:
        analyze(load(args.file))
    return 0


if __name__ == "__main__":
    sys.exit(main())
