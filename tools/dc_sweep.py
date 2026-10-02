"""Sweep a DC timing setting and measure the ASDA stale-target rate per level.

Settings:
- syncoffset: the master's SyncOffset in % (jobs/templates/set_ec_sync_offset.py,
  WANT; refused above machine.SYNC_OFFSET_MAX = 50: 60+ wedged the PLC);
- cycle: the EtherCAT cycle in us (jobs/templates/set_bus_cycle.py,
  CYCLE_US; task, master and every slave's SYNC0; the drives' 0x60C2
  follows);
- shift: the ASDA drives' "DC sync0 shift time" (jobs/templates/
  set_drive_sync_shift.py, SHIFT; the unit seems to be ns -- 100..400 had
  no effect on 2026-10-01).

Per level (in the order given; repeat a value to sample several EtherCAT
starts, the rate varies per start): set it, download with the drives off
(EtherCAT restarts), delta real, home, continuous Z 0 <-> -5 mm at F 5 for
--seconds (sync_shift_sweep.run_z), then late % / burst events per drive
and the EasyCAT frame arrival after SYNC0 over the run. At the end every
swept setting goes back to its --restore value.

    python tools/dc_sweep.py --owner-ok syncoffset 50 0 40 10 30 20 20 30 10 40 0 50
    python tools/dc_sweep.py --owner-ok shift 100000 200000 300000
"""

import argparse
import json
import os
import re
import time

import drive_param as dp
import machine as mc
from machine import log
from sync_shift_sweep import run_z, virtual

JOBS = {
    "syncoffset": ("set_ec_sync_offset.py", "WANT", "50"),
    "shift": ("set_drive_sync_shift.py", "SHIFT", "0"),
    "cycle": ("set_bus_cycle.py", "CYCLE_US", "1000"),
}


def apply(kind, value):
    job, var, _ = JOBS[kind]
    if kind == "syncoffset" and int(value) > mc.SYNC_OFFSET_MAX:
        raise SystemExit("REFUSED: SyncOffset %s > %d" % (value, mc.SYNC_OFFSET_MAX))
    src = open(os.path.join(mc.REPO, "codesys_scripts", "jobs", "templates", job), encoding="ascii").read()
    tmp = os.path.join(mc.REPO, "codesys_scripts", "jobs", "_tmp_dc_sweep.py")
    m = re.search(r'^%s = (.*)$' % var, src, flags=re.M)
    quoted = m.group(1).strip().startswith('"')
    new = '%s = "%s"' % (var, value) if quoted else '%s = %d' % (var, int(value))
    open(tmp, "w", encoding="ascii").write(src[:m.start()] + new + src[m.end():])
    try:
        out = mc.rpc("exec", "--file", tmp, timeout=300)
    finally:
        os.remove(tmp)
    if not any("saved" in l for l in out):
        raise SystemExit("job failed: %s" % "\n".join(out[-10:]))
    for line in out:
        if "->" in line:
            log("  " + line.strip())
    mc.safe_install(read_log=False)


def arrival(since_ms):
    n = mc.sys_cmd("ESP_ARR", **{"from": 0})["n"]
    rows, i = [], max(0, n - 700)
    while i < n:
        ev = mc.sys_cmd("ESP_ARR", **{"from": i})["ev"].rstrip(",")
        vals = [tuple(int(x) for x in e.split(":")) for e in ev.split(",") if e]
        if not vals:
            break
        rows += vals
        i += len(vals)
    rows = [r for r in rows if r[0] >= since_ms] or rows
    if not rows:
        return None
    lo = sorted(r[1] for r in rows)
    hi = sorted(r[2] for r in rows)
    return {"min": lo[0], "p50_min": lo[len(lo) // 2], "p50_max": hi[len(hi) // 2], "max": hi[-1]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=sorted(JOBS))
    ap.add_argument("values", nargs="+")
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--restore")
    ap.add_argument("--owner-ok", action="store_true")
    a = ap.parse_args()
    mc.require_owner_ok(a.owner_ok)
    restore = a.restore or JOBS[a.kind][2]
    # A stop file cancels the levels of a queued sweep and keeps only the
    # restore (so a chained sweep can be cut short without killing it
    # mid-motion or mid-download).
    stop = os.path.join(mc.REPO, "codesys_scripts", "jobs", "dc_sweep.stop")
    if os.path.exists(stop):
        os.remove(stop)
        log("stop file found: skipping %s, only restoring %s" % (" ".join(a.values), restore))
        a.values = []
    mc.reconnect()
    results = []
    try:
        for i, v in enumerate(a.values):
            log("=== %s %s (%d/%d) ===" % (a.kind, v, i + 1, len(a.values)))
            mc.drives_off()
            apply(a.kind, v)
            mc.reconnect()
            if a.kind == "cycle":
                from sync_shift_sweep import sdo_read
                log("  60C2 (sub1, sub2) per drive:", [(sdo_read(st, 0x60C2, 1, 1), sdo_read(st, 0x60C2, 2, 1))
                                                       for st in dp.STATIONS.values()])
            mc.set_delta(real=True)
            mc.fsm_to("Ready", timeout=180)
            r = run_z(a.seconds)
            virtual()
            arr = arrival(0)
            res = {"run": i + 1, a.kind: v, "arrival_us": arr, **r}
            results.append(res)
            log("  late %%: %s | bursts events: %s | arrival %s" % (
                " / ".join("%.2f" % r["EAxis%d" % k]["late_pct"] for k in range(3)),
                " / ".join(str(r["EAxis%d" % k]["events"]) for k in range(3)),
                "-" if not arr else "%d..%d us (p50 %d..%d)" % (arr["min"], arr["max"], arr["p50_min"], arr["p50_max"])))
            print(json.dumps(res), flush=True)
    finally:
        virtual()
        log("=== restore %s %s ===" % (a.kind, restore))
        mc.drives_off()
        apply(a.kind, restore)
    log("summary (%s: late %% EAxis0/1/2 | mean):" % a.kind)
    by = {}
    for r in results:
        by.setdefault(r[a.kind], []).append(r)
    for v, rs in by.items():
        pct = [[x["EAxis%d" % k]["late_pct"] for k in range(3)] for x in rs]
        mean = sum(sum(p) for p in pct) / (3.0 * len(pct))
        log("  %8s: %s | mean %.2f %%" % (v, "  ".join("/".join("%.2f" % q for q in p) for p in pct), mean))


if __name__ == "__main__":
    main()
