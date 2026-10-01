"""Compare drive parameter sets (the same on all three delta drives) by the
shock the motion feels and by the settling time.

A base set is written first; each level then changes some parameters from
the base ("P1.008=2,P2.002=25"; "base" = no change). Per level:
1. write the parameters by SDO to EAxis0/1/2 (drive EEPROM; the first value
   ever seen per drive and parameter stays in
   codesys_scripts/jobs/drive_params_backup.json);
2. delta real, home; the round path (4-point square +-50 at Z 0, corners
   49 mm, --speed %) for --seconds: FB_STATS torque change and position d2,
   DEM_STATS late %;
3. --settle-cycles rounds of the PnP stops of settle_test.py: settling to
   0.2 mm.
At the end the base set is written again.

    python tools/param_sweep.py --owner-ok \\
        --base P1.068=12,P1.008=0,P2.002=0,P2.025=5 \\
        --levels base P1.008=2 P2.002=25 P2.025=10
"""

import argparse
import json
import time

import drive_param as dp
import machine as mc
from machine import log
from filter_sweep import run as run_shock, tail
from settle_test import run as run_settle, summary
from sync_shift_sweep import virtual


def parse(spec):
    if spec in ("", "base"):
        return {}
    return {k.strip().upper(): int(v, 0) for k, v in (x.split("=") for x in spec.split(","))}


def write(params):
    bk = dp.load_backup()
    for name, value in sorted(params.items()):
        for k in range(3):
            bk.setdefault("EAxis%d %s" % (k, name), dp.read(name, k))
            json.dump(bk, open(dp.BACKUP, "w"), indent=1, sort_keys=True)
            dp.sdo(dp.STATIONS[k], dp.obj(name), value)
        rb = [dp.read(name, k) for k in range(3)]
        log("  %s = %s (read back %s)" % (name, value, rb))
        if rb != [value] * 3:
            raise SystemExit("%s read back %s, wanted %s" % (name, rb, value))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--levels", nargs="+", default=["base"])
    ap.add_argument("--speed", type=float, default=70)
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--cor", type=float, default=49.0)
    ap.add_argument("--settle-cycles", type=int, default=3)
    ap.add_argument("--owner-ok", action="store_true")
    a = ap.parse_args()
    mc.require_owner_ok(a.owner_ok)
    mc.reconnect()
    base = parse(a.base)
    s = a.speed / 100.0
    kin = dict(F=2000.0 * s, ACC=200000.0 * s, DEA=200000.0 * s, JERK=800000.0 * s)
    pkts = [dict(kin, type="M", cmd="G1", X=float(x), Y=float(y), Z=0.0, Cor=a.cor)
            for _ in range(int(a.seconds) * 3 + 30)
            for x, y in ((-50, -50), (50, -50), (50, 50), (-50, 50))]
    results = []
    try:
        mc.drives_off()
        log("=== base %s ===" % base)
        write(base)
        for lv in a.levels:
            over = parse(lv)
            log("=== level %s ===" % (lv or "base"))
            mc.drives_off()
            write(dict(base, **over))
            mc.set_delta(real=True)
            mc.fsm_to("Ready", timeout=180)
            shock = run_shock(pkts, a.seconds)
            stops, _ = run_settle(a.settle_cycles, a.speed, 0.4)
            virtual()
            results.append({"level": lv, "params": dict(base, **over), "shock": shock, "stops": stops})
            for k in range(3):
                x = shock["EAxis%d" % k]
                log("  EAxis%d late %.2f %% | torque change max %.1f %%, >=20 %% %d, >=50 %% %d per run | pos d2 50k-100k %d" % (
                    k, x["late_pct"], x["tq_max"] / 10.0, sum(x["tq_hist"][6:]), x["tq_hist"][7], x["d2_hist"][6]))
            log("  settle: %s" % summary(stops))
    finally:
        virtual()
        log("=== back to base %s ===" % base)
        mc.drives_off()
        write(base)
    json.dump(results, open("../codesys_scripts/jobs/param_sweep.json", "w"), indent=1)
    log("summary (torque change max % EAxis0/1/2 | >=20 % count | settle):")
    for r in results:
        sh = r["shock"]
        log("  %-12s %s | %s | %s" % (
            r["level"], "/".join("%.1f" % (sh["EAxis%d" % k]["tq_max"] / 10.0) for k in range(3)),
            "/".join(str(sum(sh["EAxis%d" % k]["tq_hist"][6:])) for k in range(3)), summary(r["stops"])))


if __name__ == "__main__":
    main()
