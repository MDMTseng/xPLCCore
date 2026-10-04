"""Compare the timing of the three delta drives' stale-target bursts.

The PLC logs, per drive, every start of a stretch where the drive's
position demand (0x6062) is one cycle late or early against the targets
sent (GVL.DemEvt, SYS DEM_EVT; cleared by DEM_STATS reset:1). This reads
the three logs, groups events less than --gap ms apart into bursts, and
prints:
- per drive: burst count and the intervals between bursts;
- per burst of each drive: the nearest burst start of the other two.

If the drives' bursts coincide (within a few ms), the cause is common
(master, SYNC0, DC). If they are unrelated, each drive's own clock / sync
loop drifts on its own.

It also reads the EasyCAT's odd SYNC0 intervals (SYS ESP_ODD: SYNC0-to-
SYNC0 off the cycle by > 3 us, timed by the ESP32). A +x / -x pair in
consecutive cycles is one late timestamp (ESP32 interrupt latency); a
single one is a real shift of SYNC0 (DC system time). For every drive
burst it prints the nearest single shift.

And the EasyCAT's frame-arrival log (SYS ESP_ARR: the output SM event
after its SYNC0, min / max per 100 ms): for every drive burst, the
arrival range in the 100 ms buckets around it, against the whole run.
--plot FILE draws arrival over time with the bursts marked.

And the SYNC0 interval at the EasyCAT in ns (SYS ESP_SYNC: interval
minus the cycle, MCPWM-capture timestamps, min / max per 100 ms): a shift
of the DC system time would show here at the bursts.

    python tools/dem_events.py [--gap 300] [--json FILE] [--plot FILE.png]

Run any real-delta motion first (e.g. square_dip.py) after DEM_STATS reset.
Only cycles where the target moves can show a lag, so stops hide bursts.
"""

import argparse
import bisect
import json

import machine as mc
from machine import log


def read(axis):
    n = mc.sys_cmd("DEM_EVT", axis=axis, **{"from": 0})["n"]
    out = []
    i = max(0, n - 1024)
    while i < n:
        ev = mc.sys_cmd("DEM_EVT", axis=axis, **{"from": i})["ev"].rstrip(",")
        vals = [int(x) for x in ev.split(",") if x]
        if not vals:
            break
        out += vals
        i += len(vals)
    return n, [(v >> 2, v & 3) for v in out]       # (ms, 1 late / 2 early)


def read_odd():
    """SYS ESP_ODD was removed from the PLC on 2026-10-05: its odd-SYNC0 log
    was never written after the ESP32 moved to the MCPWM capture (it always
    answered n=0). Kept so the JSON keeps its shape."""
    return [], [], 0


def read_arr():
    n = mc.sys_cmd("ESP_ARR", **{"from": 0})["n"]
    out = []
    i = max(0, n - 4096)
    while i < n:
        ev = mc.sys_cmd("ESP_ARR", **{"from": i})["ev"].rstrip(",")
        vals = [tuple(int(x) for x in e.split(":")) for e in ev.split(",") if e]
        if not vals:
            break
        out += vals
        i += len(vals)
    return out                                     # (ms, min us, max us)


def read_sync():
    n = mc.sys_cmd("ESP_SYNC", **{"from": 0})["n"]
    out = []
    i = max(0, n - 4096)
    while i < n:
        ev = mc.sys_cmd("ESP_SYNC", **{"from": i})["ev"].rstrip(",")
        vals = [tuple(int(x) for x in e.split(":")) for e in ev.split(",") if e]
        if not vals:
            break
        out += vals
        i += len(vals)
    return out                                     # (ms, min ns, max ns)


def bursts(events, gap):
    b = []
    for ms, code in events:
        if b and ms - b[-1]["end"] < gap:
            b[-1]["end"] = ms
            b[-1]["n"] += 1
            b[-1]["early"] += code == 2
        else:
            b.append({"start": ms, "end": ms, "n": 1, "early": int(code == 2)})
    return b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gap", type=int, default=300, help="ms between events of one burst")
    ap.add_argument("--json", help="save the raw events here")
    ap.add_argument("--plot", help="PNG: frame arrival over time with the bursts")
    ap.add_argument("--quick", action="store_true", help="skip the EasyCAT logs (arrival, SYNC0): bursts only")
    a = ap.parse_args()
    mc.reconnect()
    ev, bs = {}, {}
    for k in range(3):
        n, ev[k] = read(k)
        bs[k] = bursts(ev[k], a.gap)
        log("EAxis%d: %d events%s, %d bursts" % (k, n, " (oldest overwritten)" if n > 1024 else "", len(bs[k])))
    odd, singles, pairs = ([], [], 0) if a.quick else read_odd()
    log("EasyCAT odd SYNC0 intervals: %d events, %d +/- pairs (timestamp latency), %d single shifts" % (
        len(odd), pairs, len(singles)))
    arr = [] if a.quick else read_arr()
    if arr:
        lo = sorted(x[1] for x in arr)
        hi = sorted(x[2] for x in arr)
        log("EasyCAT frame arrival after SYNC0: %d buckets of 100 ms; min %d us, max %d us; "
            "bucket max p50 %d / p99 %d us" % (len(arr), lo[0], hi[-1], hi[len(hi) // 2], hi[int(len(hi) * 0.99)]))
    syn = [] if a.quick else read_sync()
    if syn:
        lo = sorted(x[1] for x in syn)
        hi = sorted(x[2] for x in syn)
        log("EasyCAT SYNC0 interval minus cycle: %d buckets; min %d ns, max %d ns; bucket min p1 %d, bucket max p99 %d ns" % (
            len(syn), lo[0], hi[-1], lo[int(len(lo) * 0.01)], hi[int(len(hi) * 0.99)]))
    if a.json:
        json.dump({"EAxis%d" % k: ev[k] for k in range(3)} | {"odd": odd, "arr": arr, "sync": syn}, open(a.json, "w"))
    t0 = min([b[0]["start"] for b in bs.values() if b] or [0])
    for k in range(3):
        st = [b["start"] for b in bs[k]]
        iv = [(y - x) / 1000.0 for x, y in zip(st, st[1:])]
        if iv:
            log("EAxis%d intervals (s): %s" % (k, " ".join("%.2f" % x for x in iv)))
            log("EAxis%d interval min / median / max: %.2f / %.2f / %.2f s" % (
                k, min(iv), sorted(iv)[len(iv) // 2], max(iv)))
    print()
    print("burst starts (s from the first) and the nearest burst of the other drives (ms offset):")
    for k in range(3):
        others = [j for j in range(3) if j != k]
        near_all = []
        for b in bs[k]:
            row = []
            for j in others:
                st = [x["start"] for x in bs[j]]
                if not st:
                    row.append(None)
                    continue
                i = bisect.bisect_left(st, b["start"])
                cand = [st[c] for c in (i - 1, i) if 0 <= c < len(st)]
                row.append(min((c - b["start"] for c in cand), key=abs))
            near_all.append(row)
            sh = min(((ms - b["start"], dev) for ms, dev in singles), key=lambda x: abs(x[0]), default=None)
            near = [x for x in arr if -200 <= x[0] - b["start"] <= 200]
            arr_txt = ("%d..%d us" % (min(x[1] for x in near), max(x[2] for x in near))) if near else "-"
            nsy = [x for x in syn if -200 <= x[0] - b["start"] <= 200]
            arr_txt += " | SYNC0 dev +-200 ms %s" % (("%d..%d ns" % (min(x[1] for x in nsy), max(x[2] for x in nsy))) if nsy else "-")
            print("  EAxis%d %8.3f s  len %4d ms  events %3d (early %d) | %s | SYNC0 shift %s | arrival +-200 ms %s" % (
                k, (b["start"] - t0) / 1000.0, b["end"] - b["start"], b["n"], b["early"],
                "  ".join("EAxis%d %+6s" % (j, "-" if d is None else d) for j, d in zip(others, row)),
                "-" if sh is None else "%+d ms (%+d us)" % sh, arr_txt))
        for idx, j in enumerate(others):
            d = [abs(r[idx]) for r in near_all if r[idx] is not None]
            if d:
                print("  EAxis%d vs EAxis%d: %d of %d bursts within 50 ms, %d within 500 ms" % (
                    k, j, sum(x <= 50 for x in d), len(d), sum(x <= 500 for x in d)))
        print()
    if a.plot and arr:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, (ax, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
        ta = [(x[0] - t0) / 1000.0 for x in arr]
        ax.fill_between(ta, [x[1] for x in arr], [x[2] for x in arr], step="post", color="0.6", label="frame arrival (min..max per 100 ms)")
        lo_y = min(x[1] for x in arr)
        for k, c in zip(range(3), ("tab:red", "tab:blue", "tab:green")):
            xs = [(b["start"] - t0) / 1000.0 for b in bs[k]]
            ax.plot(xs, [lo_y - 5 - 4 * k] * len(xs), "|", color=c, markersize=12, label="EAxis%d burst" % k)
        ax.set_ylabel("us after SYNC0")
        if syn:
            ts = [(x[0] - t0) / 1000.0 for x in syn]
            ax2.fill_between(ts, [x[1] for x in syn], [x[2] for x in syn], step="post", color="tab:purple", alpha=0.6,
                             label="SYNC0 interval - cycle (min..max per 100 ms)")
            ylo = min(x[1] for x in syn)
            for k, c in zip(range(3), ("tab:red", "tab:blue", "tab:green")):
                xs = [(b["start"] - t0) / 1000.0 for b in bs[k]]
                ax2.plot(xs, [ylo - 5 - 5 * k] * len(xs), "|", color=c, markersize=12)
            ax2.set_ylabel("ns")
            ax2.legend(loc="upper right")
            ax2.grid(alpha=0.3)
        ax2.set_xlabel("s")
        ax.legend(loc="upper right")
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(a.plot, dpi=110)
        log("plot:", a.plot)


if __name__ == "__main__":
    main()
