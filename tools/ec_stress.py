"""EtherCAT task timing under load (owner, 2026-09-29: DC statistics show
the odd late cycle while the machine runs).

    python tools/ec_stress.py [--plc 192.168.1.70] [--seconds 20]

Phases, each with the PLC's timing monitor (GVL.EcProf*) reset at its start
and read at its end:
  idle      drives off
  ready     drives on, nothing moving
  arm       a stream of short blended G1s around a circle, the motion
            buffer kept full (the delta kinematics and path planning)
  arm+reel  the same plus a reel move every 250 ms
  arm+reel+comm  plus back-to-back state queries on the TCP link
Only the reel is real; the script refuses unless the delta arms are
simulated. Close the UI's PLC link first; the timing monitor is read
through the CODESYS daemon (real-project config).
"""

import argparse
import math
import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plc_direct import Plc, Nak  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RPC = [sys.executable, os.path.join(REPO, "codesys_scripts", "rpc.py")]
FIELDS = ["EcProfCycles", "EcPeriodUsMin", "EcPeriodUsMax", "EcLate100", "EcLate500",
          "EcAgsmUsAvg", "EcAgsmUsMax", "EcRestUsAvg", "EcRestUsMax", "EcDcOutCycles"]


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def rpc(*args):
    r = subprocess.run(RPC + list(args), capture_output=True, text=True)
    return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr.strip()


def val(s):
    s = s.split("#")[-1]
    try:
        return float(s)
    except ValueError:
        return s


def prof_reset():
    rpc("write", "GVL.EcProfReset", "TRUE")
    rpc("logout")


def prof_read():
    out = {f: val(rpc("read", "GVL." + f)) for f in FIELDS}
    out["hist"] = [val(rpc("read", "GVL.EcPeriodHist[%d]" % i)) for i in range(6)]
    rpc("logout")
    return out


def to_ready(p):
    for _ in range(80):
        st = p.sys("GA_EV", ev=0)["st_str"]
        if st == "Ready":
            return
        ev = {"UnInited": 2, "Powered": 4, "GroupEnabled": 7, "Error": 8}.get(st)
        if ev is not None:
            try:
                p.sys("GA_EV", ev=ev)
            except Nak:
                pass
        time.sleep(0.5)
    raise RuntimeError("FSM did not reach Ready")


class Load:
    def __init__(self, p):
        self.p = p
        self.stop = threading.Event()
        self.threads = []
        self.counts = {"g1": 0, "g1_nak": 0, "reel": 0, "reel_nak": 0, "query": 0}

    def arm(self):
        k, n, r = 0, 24, 30.0
        while not self.stop.is_set():
            a = 2 * math.pi * k / n
            try:
                self.p.m("G1", X=r * math.cos(a), Y=r * math.sin(a), Z=12.0,
                         F=1000, ACC=100000, DEA=100000, JERK=400000, Cor=3.0)
                self.counts["g1"] += 1
                k += 1
            except Nak:
                self.counts["g1_nak"] += 1      # buffer full: that is the point
                time.sleep(0.01)

    def reel(self):
        while not self.stop.is_set():
            try:
                self.p.m("ReelGo", Distance=8.0, F=5000, ACC=100000, DEA=10000, JERK=100000)
                self.counts["reel"] += 1
            except Nak:
                self.counts["reel_nak"] += 1
            time.sleep(0.25)

    def comm(self):
        while not self.stop.is_set():
            self.p.sys("GET_MACHINE_STATE")
            self.p.sys("PLAN_GET")
            self.counts["query"] += 2

    def start(self, *names):
        for n in names:
            t = threading.Thread(target=getattr(self, n), daemon=True)
            t.start()
            self.threads.append(t)

    def end(self):
        self.stop.set()
        for t in self.threads:
            t.join(timeout=5)


def phase(name, seconds, p=None, loads=()):
    ld = Load(p) if loads else None
    prof_reset()
    if ld:
        ld.start(*loads)
    time.sleep(seconds)
    s = prof_read()
    if ld:
        ld.end()
    h = s["hist"]
    log("%-15s cycles %6d | period %7.1f..%7.1f us | late>100 %4d >500 %4d | |dev| <20:%d <50:%d <100:%d <200:%d <500:%d >=500:%d | AGSM avg %5.1f max %6.1f us | rest avg %4.1f max %5.1f | DC out %d%s" % (
        name, s["EcProfCycles"], s["EcPeriodUsMin"], s["EcPeriodUsMax"], s["EcLate100"], s["EcLate500"],
        *h, s["EcAgsmUsAvg"], s["EcAgsmUsMax"], s["EcRestUsAvg"], s["EcRestUsMax"], s["EcDcOutCycles"],
        ("  load %s" % ld.counts) if ld else ""))
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plc", default="192.168.1.70")
    ap.add_argument("--seconds", type=float, default=20)
    a = ap.parse_args()
    with Plc(a.plc) as p:
        ms = p.sys("GET_MACHINE_STATE")
        if (ms.get("axes_sim_mask", 0) & 7) != 7:
            raise SystemExit("REFUSED: the delta arms are not all simulated")
        p.sys("GA_EV", ev=8)
        time.sleep(1)
        phase("idle", a.seconds)
        to_ready(p)
        p.m("SetCoord1")
        p.m("G1", X=30.0, Y=0.0, Z=12.0, F=200, ACC=20000, DEA=20000, JERK=80000)
        p.m("WAIT_FOR_MOTION_STOP", timeout=15, timeout_ms=10000)
        phase("ready", a.seconds, p)
        phase("arm", a.seconds, p, ("arm",))
        phase("arm+reel", a.seconds, p, ("arm", "reel"))
        phase("arm+reel+comm", a.seconds, p, ("arm", "reel", "comm"))
        try:
            p.m("WAIT_FOR_MOTION_STOP", timeout=30, timeout_ms=20000)
        except Exception as e:
            log("wait for stop:", e)
        p.sys("GA_EV", ev=8)
        time.sleep(1)
        log("left in", p.sys("GA_EV", ev=0)["st_str"])


if __name__ == "__main__":
    main()
