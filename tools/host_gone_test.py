"""What the PLC does with a host that goes away (resource cleanup).

    python tools/host_gone_test.py [--plc 192.168.1.70] [--moves 20]

Queues --moves slow G1s without waiting for the replies, then closes the
connection at once. Expected: the moves the group had accepted run to
their end; the packets not processed yet are dropped
(GVL.HostGonePacketDropCount), so accepted + dropped = sent.

--silent: the host goes quiet instead, the socket left open (a pulled
cable, a frozen UI). Expected: after GVL.HostIdleTimeoutMs the PLC resets
the link (GVL.IdleResetCount +1) and the queued motion still runs to its
end with the FSM Ready (the heartbeat supervisor only counts it). Watched
through the CODESYS daemon, without a new connection. The delta
arms must be virtual. Close the UI's PLC link first.
"""

import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plc_direct import Plc, Nak  # noqa: E402
import topology as tp  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RPC = [sys.executable, os.path.join(REPO, "codesys_scripts", "rpc.py")]


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def rd(sym):
    out = subprocess.run(RPC + ["read", sym], capture_output=True, text=True).stdout.strip().splitlines()
    subprocess.run(RPC + ["logout"], capture_output=True)
    v = out[-1].split("#")[-1] if out else "?"
    try:
        return int(float(v))
    except ValueError:
        return v


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plc", default=tp.PLC_HOST)
    ap.add_argument("--moves", type=int, default=20)
    ap.add_argument("--silent", action="store_true")
    a = ap.parse_args()
    if a.silent:
        return silent(a)
    dropped0 = rd("GVL.HostGonePacketDropCount")
    with Plc(a.plc, ping=False) as p:
        if (p.sys("GET_MACHINE_STATE").get("axes_sim_mask", 0) & 7) != 7:
            raise SystemExit("REFUSED: the delta arms are not all simulated")
        to_ready(p)
        done0 = p.m("G1", X=0.0, Y=0.0, Z=12.0, A=0.0, F=200, ACC=20000, DEA=20000, JERK=80000,
                    Cor=0.0)["movement_id"]
        p.m("WAIT_FOR_MOTION_STOP", timeout=30, timeout_ms=25000)
        for k in range(a.moves):
            p.send_nowait({"type": "M", "cmd": "G1", "X": 20.0 if k % 2 == 0 else -20.0,
                           "F": 60, "ACC": 2000, "DEA": 2000, "JERK": 20000, "Cor": 0.0})
        time.sleep(0.3)
        log("sent %d G1s, closing the connection" % a.moves)
    time.sleep(1.0)
    with Plc(a.plc) as p:
        ms = p.sys("GET_MACHINE_STATE")
        log("reconnected: FSM %s, motion_buffer_size %s" % (ms["st_str"], ms.get("motion_buffer_size")))
        t0 = time.time()
        while p.sys("GET_MACHINE_STATE").get("motion_buffer_size", 0) > 0 and time.time() - t0 < 60:
            time.sleep(0.5)
        ms = p.sys("GET_MACHINE_STATE")
        ran = ms["last_completed_movement_id"] - done0
        dropped = rd("GVL.HostGonePacketDropCount") - dropped0
        log("motion finished after %.1f s: FSM %s, moves run %d, packets dropped %d, sum %d (sent %d)" % (
            time.time() - t0, ms["st_str"], ran, dropped, ran + dropped, a.moves))
        ok = ms["st_str"] == "Ready" and ran + dropped == a.moves and dropped > 0
        p.sys("GA_EV", ev=8)
    log("RESULT:", "PASS" if ok else "FAIL")


def silent(a):
    syms = ("GVL.IdleResetCount", "GVL.UiHeartbeatStaleCount", "GVL.HostIdleTimeoutMs")
    before = {k: rd(k) for k in syms}
    log("before:", before)
    p = Plc(a.plc, ping=False)
    if (p.sys("GET_MACHINE_STATE").get("axes_sim_mask", 0) & 7) != 7:
        raise SystemExit("REFUSED: the delta arms are not all simulated")
    to_ready(p)
    p.m("G1", X=0.0, Y=0.0, Z=12.0, A=0.0, F=200, ACC=20000, DEA=20000, JERK=80000, Cor=0.0)
    p.m("WAIT_FOR_MOTION_STOP", timeout=30, timeout_ms=25000)
    for k in range(12):
        p.send_nowait({"type": "M", "cmd": "G1", "X": 20.0 if k % 2 == 0 else -20.0,
                       "F": 60, "ACC": 2000, "DEA": 2000, "JERK": 20000, "Cor": 0.0})
    t0 = time.time()
    log("12 slow G1s queued (~10 s of motion); host now silent, socket open")
    link_lost = None
    while time.time() - t0 < 25:
        buf = rd("AxisGroupSM.MotionBufferSize")
        st = rd("AxisGroupSM.AxisGroupManagerFb._eState")
        if link_lost is None and not p.alive:
            link_lost = time.time() - t0
        log("t=%4.1f s  motion_buffer %s  FSM %s  link %s" % (time.time() - t0, buf, st, "up" if p.alive else "reset"))
        if buf == 0 and not p.alive:
            break
    after = {k: rd(k) for k in syms}
    log("after:", after)
    p.close()
    ok = (after["GVL.IdleResetCount"] == before["GVL.IdleResetCount"] + 1
          and after["GVL.UiHeartbeatStaleCount"] == before["GVL.UiHeartbeatStaleCount"] + 1
          and "Ready" in str(st) and buf == 0)
    log("link reset seen after %s s" % (None if link_lost is None else round(link_lost, 1)))
    log("RESULT:", "PASS" if ok else "FAIL")


if __name__ == "__main__":
    main()
