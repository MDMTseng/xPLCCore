"""Long soak of the delta on the circle-like round path, reporting every
--report seconds: the drives' late % (DEM_STATS) and the torque change per
cycle (FB_STATS) since the start.

    python tools/circle_soak.py --owner-ok [--speed 30] [--minutes 60]
                                [--report 60] [--tq-stop 100]

The path is param_sweep's: the 4-point square +-50 mm at Z 0 with corners
blended by --cor (49 = nearly a circle). Stops by itself when a drive's
torque change per cycle exceeds --tq-stop % rated (gearbox protection),
the FSM reports an error, or the stream ends early; also when the file
codesys_scripts/jobs/circle_soak.stop exists. At the end: home, delta
virtual. Drive parameters are not touched.
"""

import argparse
import os
import time

import machine as mc
from machine import log
from filter_sweep import home

STOP = os.path.join(mc.REPO, "codesys_scripts", "jobs", "circle_soak.stop")


def stats():
    f = mc.sys_cmd("FB_STATS")
    dm = mc.sys_cmd("DEM_STATS")
    out = []
    for k in range(3):
        ft = [int(x) for x in f["ft%d" % k].rstrip(",").split(",")]
        h = [int(x) for x in dm["dl%d" % k].rstrip(",").split(",")]
        out.append({"late": dm["dlate%d" % k], "moving": sum(h[1:]), "tq_max": f["ftm%d" % k] / 10.0,
                    "ge20": sum(ft[6:]), "ge50": ft[7]})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--speed", type=float, default=30)
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--report", type=float, default=60)
    ap.add_argument("--cor", type=float, default=49.0)
    ap.add_argument("--tq-stop", type=float, default=100.0, help="stop above this torque change per cycle, %% rated")
    ap.add_argument("--owner-ok", action="store_true")
    a = ap.parse_args()
    mc.require_owner_ok(a.owner_ok)
    if os.path.exists(STOP):
        os.remove(STOP)
    seconds = a.minutes * 60
    s = a.speed / 100.0
    kin = dict(F=2000.0 * s, ACC=200000.0 * s, DEA=200000.0 * s, JERK=800000.0 * s)
    laps = int(seconds / 0.3) + 50          # a lap takes >= ~0.5 s at 30 %: more than enough
    pkts = [dict(kin, type="M", cmd="G1", X=float(x), Y=float(y), Z=0.0, Cor=a.cor)
            for _ in range(laps) for x, y in ((-50, -50), (50, -50), (50, 50), (-50, 50))]
    mc.reconnect()
    reason = "time"
    try:
        mc.set_delta(real=True)
        mc.fsm_to("Ready", timeout=180)
        mc.sys_cmd("FB_STATS", reset=1)
        mc.sys_cmd("DEM_STATS", reset=1)
        mc.push("plc_stream_start", {"pkts": pkts, "timeoutMs": 30000})
        log("soak started: %.0f %% for %.0f min, %d packets" % (a.speed, a.minutes, len(pkts)))
        t0 = last = time.time()
        while time.time() - t0 < seconds:
            time.sleep(1)
            if os.path.exists(STOP):
                reason = "stop file"
                break
            if not mc.push("plc_stream_status", {})["running"]:
                reason = "stream ended early"
                break
            if time.time() - last >= a.report:
                last = time.time()
                st = stats()
                fsm, r = mc.fsm()
                log("%5.1f min | late %% %s | tq max %% %s | >=20 %% %s | >=50 %% %s | %s" % (
                    (last - t0) / 60.0,
                    "/".join("%.2f" % (100.0 * x["late"] / max(1, x["moving"])) for x in st),
                    "/".join("%.1f" % x["tq_max"] for x in st),
                    "/".join(str(x["ge20"]) for x in st),
                    "/".join(str(x["ge50"]) for x in st), fsm))
                if fsm == "Error":
                    reason = "FSM error %s %s" % (r.get("err_src"), r.get("err_id"))
                    break
                if max(x["tq_max"] for x in st) > a.tq_stop:
                    reason = "torque change over %.0f %%" % a.tq_stop
                    break
    finally:
        try:
            mc.push("plc_send_many_abort", {})
            while mc.push("plc_stream_status", {})["running"]:
                time.sleep(0.2)
            st = stats()
            log("END (%s) | late %% %s | tq max %% %s | >=20 %% %s | >=50 %% %s | moving cycles %s" % (
                reason,
                "/".join("%.2f" % (100.0 * x["late"] / max(1, x["moving"])) for x in st),
                "/".join("%.1f" % x["tq_max"] for x in st),
                "/".join(str(x["ge20"]) for x in st),
                "/".join(str(x["ge50"]) for x in st),
                "/".join(str(x["moving"]) for x in st)))
            home()
        finally:
            mc.fsm_to("UnInited", timeout=30)
            mc.set_delta(real=False)
            log("delta back to virtual")


if __name__ == "__main__":
    main()
