"""Long soak of the delta (round path, square with dips, or a Z stroke), reporting every
--report seconds: the drives' late % (DEM_STATS) and the torque change per
cycle (FB_STATS) since the start.

    python tools/circle_soak.py --owner-ok [--speed 30] [--minutes 60]
                                [--report 60] [--tq-stop 100] [--shape round|dip]

--shape round (default) is param_sweep's path: the 4-point square +-50 mm
at Z 0 with corners blended by --cor (49 = nearly a circle). --shape dip is
square_dip's: the same square with a dip to Z -15 at each corner, corner
distance 14, a 10 ms dwell at the bottom (the PnP-like path). Stops by itself when a drive's
torque change per cycle exceeds --tq-stop % rated (gearbox protection),
the FSM reports an error, or the stream ends early; also when the file
codesys_scripts/jobs/circle_soak.stop exists. At the end: home, delta
virtual. Drive parameters are not touched.

Every stop of the set positions (the dips' 10 ms dwells too) is logged by
the PLC (SYS DWELL, 2026-10-03): the per-minute line adds the stops, the
share settled within 0.2 mm before leaving, and the max error when
leaving. --dwell-log FILE also writes every stop to a CSV (at_ms, dwell_ms,
settle_ms or empty when not settled, err_stop_um, err_leave_um); the PLC
ring holds 4096 stops, read every --report s.
"""

import argparse
import os
import time

NOT_SETTLED = 4294967295

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


def dwell_summary():
    try:                                    # a PLC without SYS DWELL (before 2026-10-03)
        d = mc.sys_cmd("DWELL")
        h = [int(x) for x in d["hist"].rstrip(",").split(",")]
    except Exception:
        return None
    n = sum(h)
    return {"n": n, "settled_pct": 100.0 * (n - h[8]) / max(1, n), "le15_pct": 100.0 * sum(h[:5]) / max(1, n),
            "emax": d["emax"], "next": d["n"]}


def dwell_read(frm, fh):
    """Append stops frm.. to the CSV; returns the next index (skips what
    the 4096 ring already overwrote)."""
    n = mc.sys_cmd("DWELL")["n"]
    i = max(frm, n - 4000)
    while i < n:
        ev = mc.sys_cmd("DWELL", **{"from": i})["ev"].rstrip(",")
        rows = [e.split(":") for e in ev.split(",") if e.count(":") in (4, 5, 6) and all(e.split(":"))]
        if not rows:
            break
        for r in rows:
            fh.write("%s,%s,%s,%s,%s,%s,%s\n" % (r[0], r[1], "" if int(r[2]) == NOT_SETTLED else r[2], r[3], r[4],
                                             r[5] if len(r) > 5 else "", r[6] if len(r) > 6 else ""))   # err 5 / 10 ms after the stop
        i += len(rows)
    fh.flush()
    return i


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--speed", type=float, default=30)
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--report", type=float, default=60)
    ap.add_argument("--cor", type=float, default=49.0, help="corner distance for --shape round")
    ap.add_argument("--shape", choices=("round", "dip", "zud", "tri"), default="round",
                    help="zud: Z 0 <-> -zstroke at X0 Y0, 10 ms at each end (low load)")
    ap.add_argument("--zstroke", type=float, default=20.0)
    ap.add_argument("--tq-stop", type=float, default=100.0, help="stop above this torque change per cycle, %% rated")
    ap.add_argument("--dwell-log", help="CSV file for every stop")
    ap.add_argument("--virtual", action="store_true",
                    help="keep the delta virtual (no real motion): the bus, the PLC logs and the host link run as usual")
    ap.add_argument("--owner-ok", action="store_true")
    a = ap.parse_args()
    mc.require_owner_ok(a.owner_ok)
    if os.path.exists(STOP):
        os.remove(STOP)
    seconds = a.minutes * 60
    s = a.speed / 100.0
    kin = dict(F=2000.0 * s, ACC=200000.0 * s, DEA=200000.0 * s, JERK=800000.0 * s)
    square = ((-50, -50), (50, -50), (50, 50), (-50, 50))
    if a.shape == "round":
        laps = int(seconds / 0.3) + 50      # a lap takes >= ~0.5 s at 30 %: more than enough
        pkts = [dict(kin, type="M", cmd="G1", X=float(x), Y=float(y), Z=0.0, Cor=a.cor)
                for _ in range(laps) for x, y in square]
    elif a.shape == "zud":
        laps = int(seconds / 0.1) + 50
        pkts = []
        for _ in range(laps):
            for z in (-a.zstroke, 0.0):
                pkts.append(dict(kin, type="M", cmd="G1", X=0.0, Y=0.0, Z=z, Cor=0.0))
                pkts.append({"type": "M", "cmd": "G4", "P": 0.01})
    else:
        # dip: the square with a dip to Z -15 at each corner. tri (2026-10-05,
        # owner): the same dips at the corners of an equilateral triangle
        # whose vertices lie on the three arm directions (0 / 120 / 240 deg,
        # radius 70.7 mm = the square's corner radius), so every arm sees the
        # same load pattern and the three drives can be compared directly.
        import math
        corners = square if a.shape == "dip" else tuple(
            (round(50 * math.sqrt(2) * math.cos(math.radians(120 * k)), 3),
             round(50 * math.sqrt(2) * math.sin(math.radians(120 * k)), 3)) for k in range(3))
        laps = int(seconds / 0.5) + 50      # a dip lap takes ~1 s at 70 %
        pkts = []
        for _ in range(laps):
            for x, y in corners:
                pkts.append(dict(kin, type="M", cmd="G1", X=float(x), Y=float(y), Z=0.0, Cor=14.0))
                pkts.append(dict(kin, type="M", cmd="G1", X=float(x), Y=float(y), Z=-15.0, Cor=14.0))
                pkts.append({"type": "M", "cmd": "G4", "P": 0.01})
                pkts.append(dict(kin, type="M", cmd="G1", X=float(x), Y=float(y), Z=0.0, Cor=0.0))
    mc.reconnect()
    reason = "time"
    try:
        mc.set_delta(real=not a.virtual)
        mc.fsm_to("Ready", timeout=180, home=not a.virtual)
        mc.sys_cmd("FB_STATS", reset=1)
        mc.sys_cmd("DEM_STATS", reset=1)
        try:
            mc.sys_cmd("DWELL", reset=1)
        except Exception:
            pass
        fh = open(a.dwell_log, "a") if a.dwell_log else None
        if fh:
            fh.write("at_ms,dwell_ms,settle_ms,err_stop_um,err_leave_um,err_5ms_um,err_10ms_um\n")
        dw_next = 0
        mc.push("plc_stream_start", {"pkts": pkts, "timeoutMs": 30000})
        log("soak started: %s %.0f %% for %.0f min, %d packets" % (a.shape, a.speed, a.minutes, len(pkts)))
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
                try:
                    ec = mc.sys_cmd("EC_STATS")
                    log("        EtherCAT lost frames %d | rx errors %d | tx errors %d (since the download)" % (
                        ec["lost"], ec["rx_err"], ec["tx_err"]))
                except Exception:
                    pass
                dw = dwell_summary()
                if dw:
                    log("        stops %d | settled before leaving %.2f %% | settle <= 15 ms %.2f %% | err leaving max %d um" % (
                        dw["n"], dw["settled_pct"], dw["le15_pct"], dw["emax"]))
                if fh:
                    dw_next = dwell_read(dw_next, fh)
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
            try:
                mc.set_delta(real=False)
            except SystemExit as e:
                # 2026-10-03: after a bus dropout the axes sit in errorstop
                # (state 1), so drives_off() refuses; DELTA_MODE real:0
                # re-initialises them as virtual, which clears it.
                log("set_delta refused (%s): DELTA_MODE real:0 directly" % e)
                # Only for axes that are off (0) or in errorstop (1): a
                # powered or moving axis must go through drives_off().
                st = mc.axis_states()
                if any(s not in (0, 1) for s in st):
                    raise SystemExit("delta axes %s not off/errorstop: not switching to virtual" % st)
                mc.sys_cmd("DELTA_MODE", real=0)
                for _ in range(40):
                    if mc.delta_mask() == 7:
                        break
                    time.sleep(0.5)
            log("delta back to virtual (mask %s, axes %s)" % (mc.delta_mask(), mc.axis_states()))


if __name__ == "__main__":
    main()
