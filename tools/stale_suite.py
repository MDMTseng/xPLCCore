"""After-recovery test suite for the ASDA stale-target investigation
(doc_review/asda_stale_target_2026-09-30.md, section 7c). Through the UI's
link (standalone UI with XPLC_HARNESS=1). Real delta motion: the owner must
have OK'd it.

    python tools/stale_suite.py repeat [--n 3]      # SyncOffset as is; n x (download + 1 min pulse test)
    python tools/stale_suite.py long [--minutes 5]  # single joint up/down at 0.1 deg/s for N minutes
    python tools/stale_suite.py group [--minutes 5] # home, 1/10 square at 30 % through the axis group

Every result line is JSON with the drive-demand figures (DEM_STATS): lag
histogram (0..6, none), late = lag nominal+1 (stale), late_pct of the moving
cycles, d2 max; plus EasyCAT frame arrival and lost frames.
Never set SyncOffset >= 60 (the bus stayed in BOOT, 2026-09-30).
"""

import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "sim"))
sys.argv, _argv = sys.argv[:1], sys.argv
import run_virtual as rv  # noqa: E402
sys.argv = _argv

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def plc(pkt, timeout_ms=5000):
    return rv.push("plc_send", {"pkt": pkt, "timeoutMs": timeout_ms}, timeout=timeout_ms / 1000 + 5)


def run(*args, timeout=600):
    r = subprocess.run([PY] + list(args), cwd=REPO, capture_output=True, text=True, timeout=timeout)
    return (r.stdout + r.stderr).strip().splitlines()


def reconnect():
    for _ in range(20):
        try:
            plc({"type": "SYS", "cmd": "PING"}, 3000)
            return
        except Exception:
            try:
                rv.push("disconnect_tcp", {}, timeout=20)
                time.sleep(2)
                rv.push("connect_tcp", {}, timeout=20)
                time.sleep(2)
            except Exception:
                time.sleep(3)
    raise SystemExit("UI link to the PLC did not come back")


def bus_ok():
    e = plc({"type": "SYS", "cmd": "EC_STATS"})
    js = plc({"type": "SYS", "cmd": "JOINT_STATE"})
    return e, js


def fsm_to(want, home_ev=6):
    for _ in range(200):
        r = plc({"type": "SYS", "cmd": "GA_EV", "ev": 0})
        st = r["st_str"]
        if st == want:
            return
        if want == "Powered":
            ev = {"UnInited": 2}.get(st, 8)
        else:
            ev = {"UnInited": 2, "Powered": 4, "GroupEnabled": home_ev, "Error": 8}.get(st)
        if ev is not None:
            try:
                plc({"type": "SYS", "cmd": "GA_EV", "ev": ev})
            except Exception:
                pass
        time.sleep(0.5)
    raise SystemExit("FSM did not reach %s (%s, %s)" % (want, st, r.get("err_src")))


def drives_off():
    run("tools/joint_bench.py", "uninited", timeout=90)
    st = [plc({"type": "SYS", "cmd": "AXIS_INFO", "axis": k})["st"] for k in range(3)]
    if st != [0, 0, 0]:
        raise SystemExit("delta not powered off: %s" % st)


def real():
    run("tools/joint_bench.py", "real", timeout=120)
    if (plc({"type": "SYS", "cmd": "GET_MACHINE_STATE"}).get("axes_sim_mask", 7) & 7) != 0:
        raise SystemExit("delta not real")


def reset_stats():
    plc({"type": "SYS", "cmd": "EC_STATS", "reset": 1})
    plc({"type": "SYS", "cmd": "DEM_STATS", "reset": 1})
    time.sleep(1)


def result(label, extra=None):
    dm = plc({"type": "SYS", "cmd": "DEM_STATS"})
    e = plc({"type": "SYS", "cmd": "EC_STATS"})
    out = {"test": label, "lagnom": dm["lagnom"], "lost": e["lost"],
           "arrival_us": [e["e_amin"], e["e_amax"], e["e_apeak"]], "easycat_stale": e["e_stale"]}
    for k in range(3):
        h = [int(x) for x in dm["dl%d" % k].rstrip(",").split(",")]
        moving = sum(h[1:])
        out["EAxis%d" % k] = {"hist": h, "late": dm["dlate%d" % k], "stale": dm["ds%d" % k],
                              "late_pct": round(100.0 * dm["dlate%d" % k] / max(1, moving), 2),
                              "d2max": dm["dmx%d" % k]}
    out.update(extra or {})
    print(json.dumps(out), flush=True)
    return out


def pulse(seconds):
    """EAxis0 up and down at 0.1 deg/s for about `seconds`."""
    fsm_to("Powered")
    reset_stats()
    t0 = time.time()
    d = 1.5
    while time.time() - t0 < seconds:
        plc({"type": "SYS", "cmd": "JOINT_MOVE", "axis": 0, "dist": d, "vel": 0.1})
        t1 = time.time()
        while time.time() - t1 < abs(d) / 0.1 + 10:
            time.sleep(2)
            if plc({"type": "SYS", "cmd": "JOINT_STATE"})["s0"] != 1:
                break
        d = -d
    if d < 0:                                  # ended up: come back down
        plc({"type": "SYS", "cmd": "JOINT_MOVE", "axis": 0, "dist": d, "vel": 0.1})
        time.sleep(abs(d) / 0.1 + 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=("repeat", "long", "group"))
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--minutes", type=float, default=5)
    a = ap.parse_args()
    reconnect()
    e, js = bus_ok()
    if not js.get("v0", True) and js.get("p0", 0) == 0 and js.get("p1", 0) == 0:
        log("warning: joint positions all 0 -- is EtherCAT up?")
    if a.what == "repeat":
        for i in range(a.n):
            drives_off()
            lines = run("codesys_scripts/rpc.py", "install", "--on-site", timeout=600)
            log("download:", [l for l in lines if "download done" in l])
            reconnect()
            real()
            pulse(60)
            result("repeat %d/%d" % (i + 1, a.n))
    elif a.what == "long":
        real()
        pulse(a.minutes * 60)
        result("long %.0f min" % a.minutes)
    else:
        real()
        fsm_to("Ready")
        reset_stats()
        lines = run("tools/square_dip.py", "--continuous", "--minutes", str(a.minutes), "--half", "5",
                    "--dip", "-1.5", "--cor", "1.4", "--f", "600", "--acc", "60000", "--jerk", "240000",
                    timeout=a.minutes * 60 + 300)
        log("square:", lines[-1] if lines else "?")
        result("group square 1/10 30%% %.0f min" % a.minutes)


if __name__ == "__main__":
    main()
