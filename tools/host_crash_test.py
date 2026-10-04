"""The host PC dies without closing its PLC connection (a blue screen):
can it connect again after it comes back?

    python tools/host_crash_test.py [--plc 192.168.1.70] [--nic 以太网] [--down 90]

Run as administrator on the PC that talks to the PLC through its own NIC
(this disables that NIC for --down seconds; the internet is elsewhere).
Steps: connect and ping; disable the NIC; drop the socket abortively (no
FIN reaches the PLC, like a crash); wait --down s (a reboot); enable the
NIC; then try to connect and ping for up to 60 s, and read the PLC's TCP
server state through the CODESYS daemon. No motion is commanded.
"""

import argparse
import os
import socket
import struct
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plc_direct import Plc  # noqa: E402
import topology as tp  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RPC = [sys.executable, os.path.join(REPO, "codesys_scripts", "rpc.py")]
STATE = ("GVL.IdleResetCount", "GVL.ClientConnectCount", "GVL.ReadErrorResetCount",
         "TCP_MSGPAK_Server.fbMyServer.xResetting", "TCP_MSGPAK_Server.fbMyServer.CLIENT.xActive",
         "TCP_MSGPAK_Server.fbMyServer.xServerActive")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def ps(cmd):
    return subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True)


def plc_state():
    out = {}
    for s in STATE:
        r = subprocess.run(RPC + ["read", s], capture_output=True, text=True).stdout.strip().splitlines()
        out[s.split(".")[-1]] = r[-1].split("#")[-1] if r else "?"
    subprocess.run(RPC + ["logout"], capture_output=True)
    return out


def pingable(host):
    return subprocess.run(["ping", "-n", "1", "-w", "500", host], capture_output=True).returncode == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plc", default=tp.PLC_HOST)
    ap.add_argument("--nic", default="以太网")
    ap.add_argument("--down", type=float, default=90)
    ap.add_argument("--traffic", action="store_true",
                    help="queue 20 slow G1s first: the PLC keeps answering the dead host (delta arms must be virtual)")
    a = ap.parse_args()
    log("PLC TCP server before:", plc_state())
    p = Plc(a.plc)
    p.sys("PING")
    log("connected, pinging")
    if a.traffic:
        if (p.sys("GET_MACHINE_STATE").get("axes_sim_mask", 0) & 7) != 7:
            raise SystemExit("REFUSED: the delta arms are not all simulated")
        for _ in range(80):
            st = p.sys("GA_EV", ev=0)["st_str"]
            if st == "Ready":
                break
            try:
                p.sys("GA_EV", ev={"UnInited": 2, "Powered": 4, "GroupEnabled": 7, "Error": 8}.get(st))
            except Exception:
                pass
            time.sleep(0.5)
        p.m("G1", X=0.0, Y=0.0, Z=12.0, F=200, ACC=20000, DEA=20000, JERK=80000, Cor=0.0)
        for k in range(20):
            p.send_nowait({"type": "M", "cmd": "G1", "X": 20.0 if k % 2 == 0 else -20.0,
                           "F": 60, "ACC": 2000, "DEA": 2000, "JERK": 20000, "Cor": 0.0})
        for _ in range(6):
            p.send_nowait({"type": "SYS", "cmd": "GET_DIAG"})
        log("20 slow G1s + GET_DIAGs queued: the PLC keeps replying")
    time.sleep(2)
    r = ps("Disable-NetAdapter -Name '%s' -Confirm:$false" % a.nic)
    log("NIC disabled", r.returncode, r.stderr.strip()[:200])
    # abortive close: RST instead of FIN, and it cannot leave the PC anyway
    p.alive = False
    p.sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    p.sock.close()
    time.sleep(a.down)
    r = ps("Enable-NetAdapter -Name '%s' -Confirm:$false" % a.nic)
    log("NIC enabled", r.returncode, r.stderr.strip()[:200])
    t0 = time.time()
    while not pingable(a.plc) and time.time() - t0 < 60:
        time.sleep(0.5)
    log("PLC answers ping after %.1f s" % (time.time() - t0))
    log("PLC TCP server now:", plc_state())
    ok_at = None
    t1 = time.time()
    while time.time() - t1 < 60:
        try:
            with Plc(a.plc, timeout=3, ping=False) as q:
                q.sys("PING", timeout=3)
                ok_at = time.time() - t1
                break
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, e)
            time.sleep(2)
    if ok_at is None:
        log("could NOT connect within 60 s; last error:", last)
        log("PLC TCP server:", plc_state())
    else:
        log("reconnected and pinged after %.1f s" % ok_at)
    log("RESULT:", "PASS" if ok_at is not None else "FAIL")


if __name__ == "__main__":
    main()
