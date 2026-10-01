"""One measurement for comparing drive filter settings (e.g. P1.068) within
one EtherCAT start: home, run the 1/10 square at 30 % for --seconds, then
print the following-error jumps per cycle (EC_STATS fj/fm: how hard the
drives knock) and the drive-demand late % (DEM_STATS). Keep the bus up
between the runs you compare: each EtherCAT start lands the drives on a
different phase.

    python tools/filter_compare.py --label baseline --owner-ok
"""

import argparse
import json

import machine as mc
import stale_suite as ss


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="")
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--owner-ok", action="store_true")
    a = ap.parse_args()
    mc.require_owner_ok(a.owner_ok)
    if mc.is_delta_virtual():
        raise SystemExit("delta is virtual: make it real first (joint_bench.py real)")
    mc.fsm_to("Ready", home=True)
    ss.reset_stats()
    mc.run_tool("square_dip.py", "--continuous", "--minutes", str(a.seconds / 60.0), "--half", "5", "--dip", "-1.5",
                "--cor", "1.4", "--f", "600", "--acc", "60000", "--jerk", "240000", "--owner-ok",
                timeout=a.seconds + 300)
    e = mc.sys_cmd("EC_STATS")
    r = ss.result(a.label)
    print(json.dumps({"label": a.label,
                      "fe_jump_count": [e["fj%d" % k] for k in range(3)],
                      "fe_jump_max_deg": [round(e["fm%d" % k], 4) for k in range(3)],
                      "late_pct": [r["EAxis%d" % k]["late_pct"] for k in range(3)]}), flush=True)


if __name__ == "__main__":
    main()
