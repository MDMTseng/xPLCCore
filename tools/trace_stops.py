"""Position traces around a stop, per drive parameter set, plotted together.

Per level (parameter sets as in param_sweep.py): write the set on the three
drives, delta real, home, then two PnP stops at --speed %:
- horizontal: X -50 -> X +50 at Z 0;
- dip: X +50, Z 0 -> Z -15.
After each stop the PLC's trace (SYS SETTLE_TRACE: 100 cycles before the
set positions stopped and 300 after, commanded and actual TCP, mm) is read.
The plot (--plot) has, per stop: the position along the move minus the
final actual position (actual solid, commanded dashed), and the distance
of the actual TCP to its final position with the 0.2 mm band.

    python tools/trace_stops.py --owner-ok \\
        --base P1.068=12,P1.008=0,P2.002=0,P2.025=5 \\
        --levels "none:P1.068=0" "P1.068 12 ms:" "B:P1.008=2" --plot FILE.png
"""

import argparse
import json
import math
import time

import machine as mc
from machine import log
from param_sweep import parse, write
from sync_shift_sweep import virtual


def wait_stop():
    mc.plc({"type": "M", "cmd": "WAIT_FOR_MOTION_STOP", "timeout": 30, "timeout_ms": 12000}, 13000)


def read_trace(seq_before):
    for _ in range(50):
        if mc.sys_cmd("SETTLE_TRACE", **{"from": 0})["seq"] > seq_before:
            break
        time.sleep(0.1)
    rows = []
    i = 0
    while i < 400:
        ev = mc.sys_cmd("SETTLE_TRACE", **{"from": i})["ev"]
        # The reply holds 255 chars: the last sample can be cut off, even
        # inside a number ("49.98" -> "4"). Only samples followed by a comma
        # are complete; the rest is read next time.
        vals = []
        for e in ev.split(",")[:-1]:
            try:
                v = tuple(float(x) for x in e.split(":"))
            except ValueError:
                break
            if len(v) != 6:
                break
            vals.append(v)
        if not vals:
            break
        rows += vals
        i += len(vals)
    return rows                                     # (cx, cy, cz, ax, ay, az), 100 = the stop


def stop(kin, x, z):
    seq = mc.sys_cmd("SETTLE_TRACE", **{"from": 0})["seq"]
    mc.plc(dict(kin, type="M", cmd="G1", X=float(x), Y=0.0, Z=float(z)))
    wait_stop()
    time.sleep(0.5)                                 # > 300 cycles after the stop
    return read_trace(seq)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--levels", nargs="+", required=True, help='"name:P1.008=2,..." (empty = base)')
    ap.add_argument("--speed", type=float, default=70)
    ap.add_argument("--plot")
    ap.add_argument("--owner-ok", action="store_true")
    a = ap.parse_args()
    mc.require_owner_ok(a.owner_ok)
    mc.reconnect()
    base = parse(a.base)
    s = a.speed / 100.0
    kin = dict(F=2000.0 * s, ACC=200000.0 * s, DEA=200000.0 * s, JERK=800000.0 * s, Cor=0.0)
    traces = {}
    try:
        for lv in a.levels:
            name, _, spec = lv.partition(":")
            log("=== %s ===" % name)
            mc.drives_off()
            write(dict(base, **parse(spec)))
            mc.set_delta(real=True)
            mc.fsm_to("Ready", timeout=180)
            mc.plc(dict(kin, type="M", cmd="G1", X=-50.0, Y=0.0, Z=0.0))
            wait_stop()
            time.sleep(0.3)
            horiz = stop(kin, 50, 0)
            dip = stop(kin, 50, -15)
            mc.plc(dict(kin, type="M", cmd="G1", X=0.0, Y=0.0, Z=0.0))
            wait_stop()
            virtual()
            traces[name] = {"horizontal": horiz, "dip": dip}
            log("  traces: horizontal %d samples, dip %d samples" % (len(horiz), len(dip)))
    finally:
        virtual()
        log("=== back to base %s ===" % base)
        mc.drives_off()
        write(base)
    json.dump(traces, open("../codesys_scripts/jobs/trace_stops.json", "w"))
    if a.plot:
        plot(traces, a.plot)


def plot(traces, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colors = ["tab:red", "tab:blue", "tab:green", "tab:purple"]
    fig, axes = plt.subplots(2, 2, figsize=(14, 9))
    for col, (stop_name, axis, label) in enumerate((("horizontal", 0, "X"), ("dip", 2, "Z"))):
        ax1, ax2 = axes[0][col], axes[1][col]
        for (name, tr), c in zip(traces.items(), colors):
            rows = tr[stop_name]
            if len(rows) < 400:
                continue
            fa = rows[-1][3:]                       # final actual position
            t = [k - 100 for k in range(len(rows))]
            ax1.plot(t, [r[3 + axis] - fa[axis] for r in rows], color=c, label="%s actual" % name)
            ax1.plot(t, [r[axis] - fa[axis] for r in rows], color=c, ls="--", lw=1, label="%s command" % name)
            d = [math.sqrt(sum((r[3 + j] - fa[j]) ** 2 for j in range(3))) for r in rows]
            ax2.plot(t, d, color=c, label=name)
            settle = next((k for k in range(len(d) - 1, -1, -1) if d[k] > 0.2), -1) + 1 - 100
            ax2.axvline(settle, color=c, ls=":", lw=1)
        for ax in (ax1, ax2):
            ax.axvline(0, color="k", lw=0.8)
            ax.grid(alpha=0.3)
            ax.set_xlim(-60, 60)
        ax1.set_ylim(-3, 1)
        ax1.set_title("%s stop: %s minus final position (0 = command end)" % (stop_name, label))
        ax1.set_ylabel("mm")
        ax1.legend(fontsize=8)
        ax2.axhline(0.2, color="k", ls="--", lw=0.8)
        ax2.set_yscale("log")
        ax2.set_ylim(0.005, 10)
        ax2.set_title("%s stop: distance of the actual TCP to its final position" % stop_name)
        ax2.set_xlabel("ms after the command end (dotted: settled within 0.2 mm)")
        ax2.set_ylabel("mm")
        ax2.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    log("plot:", path)


if __name__ == "__main__":
    main()
