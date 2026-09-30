"""After-recovery test suite for the ASDA stale-target investigation
(doc_review/asda_stale_target_2026-09-30.md, sections 7c and 8). Through
the UI's link (standalone UI with XPLC_HARNESS=1). Moves the real delta:
needs --owner-ok.

    python tools/stale_suite.py repeat [--n 3] --owner-ok      # n x (safe download + 1 min pulse test)
    python tools/stale_suite.py long [--minutes 5] --owner-ok  # single joint up/down at 0.1 deg/s
    python tools/stale_suite.py group [--minutes 5] --owner-ok # home, 1/10 square at 30 % through the group

Each result is one JSON line with the drive-demand figures (DEM_STATS):
- lag histogram (0..6, none);
- late = lag nominal + 1 (stale), and late_pct of the moving cycles;
- demand d2 max;
- EasyCAT frame arrival and lost frames.

Keep SyncOffset <= 50 (machine.SYNC_OFFSET_MAX).
"""

import argparse
import json
import time

import machine as mc
from machine import log


def reset_stats():
    mc.sys_cmd("EC_STATS", reset=1)
    mc.sys_cmd("DEM_STATS", reset=1)
    time.sleep(1)


def result(label):
    dm = mc.sys_cmd("DEM_STATS")
    e = mc.sys_cmd("EC_STATS")
    out = {"test": label, "lagnom": dm["lagnom"], "lost": e["lost"],
           "arrival_us": [e["e_amin"], e["e_amax"], e["e_apeak"]], "easycat_stale": e["e_stale"]}
    for k in range(3):
        h = [int(x) for x in dm["dl%d" % k].rstrip(",").split(",")]
        moving = sum(h[1:])
        out["EAxis%d" % k] = {"hist": h, "late": dm["dlate%d" % k], "stale": dm["ds%d" % k],
                              "late_pct": round(100.0 * dm["dlate%d" % k] / max(1, moving), 2),
                              "d2max": dm["dmx%d" % k]}
    print(json.dumps(out), flush=True)
    return out


def pulse(seconds):
    """EAxis0 up and down by 1.5 deg at 0.1 deg/s for about `seconds`."""
    mc.fsm_to("Powered")
    reset_stats()
    t0 = time.time()
    d = 1.5
    while time.time() - t0 < seconds:
        mc.sys_cmd("JOINT_MOVE", axis=0, dist=d, vel=0.1)
        t1 = time.time()
        while time.time() - t1 < abs(d) / 0.1 + 10:
            time.sleep(2)
            if mc.sys_cmd("JOINT_STATE")["s0"] != 1:
                break
        d = -d
    if d < 0:                                  # ended up: come back down
        mc.sys_cmd("JOINT_MOVE", axis=0, dist=d, vel=0.1)
        time.sleep(abs(d) / 0.1 + 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=("repeat", "long", "group"))
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--minutes", type=float, default=5)
    ap.add_argument("--owner-ok", action="store_true", help="the owner OK'd moving the real delta")
    a = ap.parse_args()
    mc.require_owner_ok(a.owner_ok)
    mc.reconnect()
    ok, ec = mc.bus_up()
    if not ok:
        raise SystemExit("EtherCAT is not up: %s" % ec)
    if a.what == "repeat":
        for i in range(a.n):
            mc.safe_install(read_log=(i == 0))
            mc.set_delta(real=True)
            pulse(60)
            result("repeat %d/%d" % (i + 1, a.n))
    elif a.what == "long":
        mc.set_delta(real=True)
        pulse(a.minutes * 60)
        result("long %.0f min" % a.minutes)
    else:
        mc.set_delta(real=True)
        mc.fsm_to("Ready")
        reset_stats()
        lines = mc.run_tool("square_dip.py", "--continuous", "--minutes", str(a.minutes), "--half", "5",
                            "--dip", "-1.5", "--cor", "1.4", "--f", "600", "--acc", "60000", "--jerk", "240000",
                            "--owner-ok", timeout=a.minutes * 60 + 300)
        log("square:", lines[-1] if lines else "?")
        result("group square 1/10 30%% %.0f min" % a.minutes)


if __name__ == "__main__":
    main()
