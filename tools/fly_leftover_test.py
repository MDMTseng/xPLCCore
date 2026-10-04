"""Fly events a run leaves behind: the default TTL and SYS FLUSH.

    python tools/fly_leftover_test.py [--plc 192.168.1.70]

An M4 aimed at a movement that is never sent (motion_id_offset 5) stands
for an event whose run stopped first. Checked:
  1. without ttl_ms it expires after GVL.FlyEventDefaultTtlMs (set to 2 s
     here with SET_FLY_TTL): TRIGGER_ERR 100 with its event_id, slot free;
  2. SYS FLUSH drops one at once: fly_flushed 1, TRIGGER_ERR 101, slot free;
     so does an M4 with ttl_ms -1 (never expires);
  3. FLUSH ends a pending WAIT_FOR_MOTION_STOP with NAK 'flushed'.
The TTL is set back to 10 s at the end. The pin (CAM_Side_Light0) is never
switched: the events never fire. Delta arms virtual; close the UI's link.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plc_direct import Plc, Nak  # noqa: E402
import topology as tp  # noqa: E402

PIN = 1 << 9      # CAM_Side_Light0: harmless if it ever fired
FAILS = []


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def check(cond, what):
    log(("ok    " if cond else "FAIL  ") + what)
    if not cond:
        FAILS.append(what)


def to_ready(p):
    for _ in range(80):
        st = p.sys("GA_EV", ev=0)["st_str"]
        if st == "Ready":
            return
        ev = {"UnInited": 2, "Powered": 4, "GroupEnabled": 7, "Error": 8}.get(st)
        try:
            p.sys("GA_EV", ev=ev)
        except Nak:
            pass
        time.sleep(0.5)
    raise RuntimeError("FSM did not reach Ready")


def avail(p):
    return p.sys("GET_DIAG").get("flyevent_avail")


def future_m4(p, event_id, **kw):
    return p.m("M4", pin_op_seq=[0, PIN, PIN], motion_id_offset=5, motion_progress=1, event_id=event_id, **kw)


SEEN = []


def trigger_errs(p, event_id, wait_s):
    end = time.time() + wait_s
    while True:
        SEEN.extend(p.take_events("TRIGGER_ERR"))
        evs = [e for e in SEEN if e.get("event_id") == event_id]
        if evs or time.time() > end:
            return evs
        time.sleep(0.05)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plc", default=tp.PLC_HOST)
    a = ap.parse_args()
    with Plc(a.plc) as p:
        if (p.sys("GET_MACHINE_STATE").get("axes_sim_mask", 0) & 7) != 7:
            raise SystemExit("REFUSED: the delta arms are not all simulated")
        to_ready(p)
        p.m("G1", X=0.0, Y=0.0, Z=12.0, F=200, ACC=20000, DEA=20000, JERK=80000, Cor=0.0)
        p.m("WAIT_FOR_MOTION_STOP", timeout=30, timeout_ms=25000)
        p.sys("FLUSH")
        free0 = avail(p)
        log("free fly slots:", free0)

        # 1. default TTL
        r = p.sys("SET_FLY_TTL", ttl_ms=2000)
        check(r.get("ttl_ms") == 2000, "SET_FLY_TTL 2000 -> %s" % r.get("ttl_ms"))
        t0 = time.time()
        future_m4(p, 9101)
        check(avail(p) == free0 - 1, "M4 without ttl_ms holds a slot")
        evs = trigger_errs(p, 9101, 4.0)
        dt = time.time() - t0
        check(bool(evs) and evs[0].get("error_code") == 100 and 1.8 < dt < 3.0,
              "expired after %.2f s with TRIGGER_ERR %s" % (dt, evs[0].get("error_code") if evs else None))
        check(avail(p) == free0, "slot free again")

        # 2. FLUSH
        p.sys("SET_FLY_TTL", ttl_ms=10000)
        future_m4(p, 9102)
        future_m4(p, 9103, ttl_ms=-1)
        check(avail(p) == free0 - 2, "two leftovers held (default TTL, never-expiring)")
        r = p.sys("FLUSH")
        check(r.get("fly_flushed") == 2, "FLUSH -> fly_flushed %s" % r.get("fly_flushed"))
        e2 = trigger_errs(p, 9102, 1.0)
        e3 = trigger_errs(p, 9103, 1.0)
        check(bool(e2) and bool(e3) and e2[0].get("error_code") == 101 and e3[0].get("error_code") == 101,
              "both reported TRIGGER_ERR 101")
        check(avail(p) == free0, "slots free again")

        # 3. FLUSH ends a pending wait
        p.m("G1", X=15.0, F=20, ACC=2000, DEA=2000, JERK=20000, Cor=0.0)      # ~0.8 s
        wid = p.send_nowait({"type": "M", "cmd": "WAIT_FOR_MOTION_STOP", "timeout_ms": 25000})
        time.sleep(0.1)
        p.sys("FLUSH")
        end = time.time() + 2
        rep = None
        while time.time() < end and rep is None:
            with p.lock:
                rep = p.replies.pop(wid, None)
            time.sleep(0.02)
        check(rep is not None and rep.get("ack") is False and rep.get("err") == "flushed",
              "pending WAIT_FOR_MOTION_STOP -> %s" % str(rep and (rep.get("ack"), rep.get("err"))))
        p.m("WAIT_FOR_MOTION_STOP", timeout=30, timeout_ms=25000)
        p.m("G1", X=0.0, F=200, ACC=20000, DEA=20000, JERK=80000, Cor=0.0)
        p.m("WAIT_FOR_MOTION_STOP", timeout=30, timeout_ms=25000)
        check(p.sys("GA_EV", ev=0)["st_str"] == "Ready", "FSM still Ready")
        p.sys("SET_FLY_TTL", ttl_ms=10000)
        p.sys("GA_EV", ev=8)
    log("RESULT:", "PASS" if not FAILS else "FAIL: " + "; ".join(FAILS))


if __name__ == "__main__":
    main()
