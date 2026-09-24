"""Host->PLC queue behaviour test (review 2026-09-24 P0-3), direct TCP.

    python tools/sim/queue_test.py [--plc 192.168.1.70]

Talks msgpack to PLC :8125 itself, so the UI must NOT be connected (the
PLC takes one client). Refuses unless the delta arms are virtual (read
through the CODESYS daemon). Moves: only small Z moves of the (virtual)
delta group; EAXIS_A is never commanded.

Scenarios: (1) an input wait that never resolves must not hold SYS or
motion traffic behind it, a second input wait NAKs wait_busy, the first
times out; (2) WAIT_FOR_MOTION_STOP acks when the move ends; (3) a
WAIT_FOR_REEL_STOP does not hold a G1 behind it; (4) a pending wait is
NAK'd group_not_ready when the FSM leaves Ready; (5) the heartbeat
supervisor trips when the host goes silent while a motion packet waits at
the queue head (it used to be kept alive by that packet). Ends in UnInited.
"""

import argparse
import os
import socket
import sys
import threading
import time

import msgpack

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "codesys_scripts"))
import rpc  # noqa: E402


class Plc:
    def __init__(self, host, port=8125):
        self.s = socket.create_connection((host, port), timeout=5)
        self.s.settimeout(0.2)
        self.lock = threading.Lock()
        self.replies = {}          # id -> (t_rx, msg)
        self.next_id = 1000
        self.alive = True
        self.heartbeat = True
        threading.Thread(target=self._rx, daemon=True).start()
        threading.Thread(target=self._heartbeat, daemon=True).start()

    def _rx(self):
        unp = msgpack.Unpacker(raw=False, strict_map_key=False)
        while self.alive:
            try:
                chunk = self.s.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            unp.feed(chunk)
            for msg in unp:
                if isinstance(msg, dict) and "id" in msg:
                    with self.lock:
                        self.replies[msg["id"]] = (time.time(), msg)

    def _heartbeat(self):
        while self.alive:
            if self.heartbeat:
                self.send({"type": "SYS", "cmd": "PING"})
            time.sleep(1.0)

    def send(self, pkt):
        with self.lock:
            self.next_id += 1
            i = self.next_id
        pkt = dict(pkt, id=i)
        self.s.sendall(msgpack.packb(pkt, use_bin_type=True))
        return i, time.time()

    def wait(self, i, timeout=10.0):
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                if i in self.replies:
                    return self.replies[i]
            time.sleep(0.002)
        return None, None

    def call(self, pkt, timeout=10.0):
        i, _ = self.send(pkt)
        return self.wait(i, timeout)[1]

    def close(self):
        self.alive = False
        self.s.close()


def to_ready(plc):
    for _ in range(60):
        st = (plc.call({"type": "SYS", "cmd": "GA_EV", "ev": 0}) or {}).get("st_str")
        if st == "Ready":
            return
        ev = {"UnInited": 2, "Powered": 4, "GroupEnabled": 7, "Error": 8}.get(st)
        if ev:
            plc.call({"type": "SYS", "cmd": "GA_EV", "ev": ev})
        time.sleep(0.5)
    raise SystemExit("FSM did not reach Ready")


def timed(plc, name, i, ts, lo=None, hi=None, ack=None, err=None, timeout=8.0):
    """Wait for reply i; check its latency window and ack/err. Returns fail count."""
    t_rx, msg = plc.wait(i, timeout)
    if t_rx is None:
        print("  %-26s NO REPLY  ** FAIL" % name)
        return 1
    dt = (t_rx - ts) * 1000
    bad = []
    if lo is not None and dt < lo:
        bad.append("expected >= %d ms" % lo)
    if hi is not None and dt > hi:
        bad.append("expected <= %d ms" % hi)
    if ack is not None and msg.get("ack") != ack:
        bad.append("expected ack=%s" % ack)
    if err is not None and msg.get("err") != err:
        bad.append("expected err=%s" % err)
    print("  %-26s %6.0f ms  ack=%-5s err=%-16s %s" % (
        name, dt, msg.get("ack"), msg.get("err", ""), ("** FAIL: " + ", ".join(bad)) if bad else "ok"))
    return 1 if bad else 0


DIN_NEVER = {"type": "M", "cmd": "BLOCK_FOR_DIGITAL_INPUT", "pin": 0x80, "group": 0, "state": 0x80}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plc", default="192.168.1.70")
    a = ap.parse_args()

    for ax in ("EAxis0", "EAxis1", "EAxis2"):
        v = rpc.call({"cmd": "read", "symbol": "IoConfig_Globals.%s.bVirtual" % ax}, 30).get("value", "")
        if str(v).upper() != "TRUE":
            raise SystemExit("ABORT: %s is not virtual (%s)" % (ax, v))
    rpc.call({"cmd": "logout"}, 30)

    plc = Plc(a.plc)
    f = 0
    try:
        to_ready(plc)
        print("Ready; SetCoord1 ->", plc.call({"type": "M", "cmd": "SetCoord1"}))
        print("G1 Z10 ->", plc.call({"type": "M", "cmd": "G1", "Z": 10}))
        time.sleep(1.0)

        print("\n1. input wait that never resolves (3 s), traffic behind it")
        blk, t0 = plc.send(dict(DIN_NEVER, timeout_ms=3000))
        time.sleep(0.05)
        f += timed(plc, "PING", *plc.send({"type": "SYS", "cmd": "PING"}), hi=200)
        f += timed(plc, "GA_EV(0)", *plc.send({"type": "SYS", "cmd": "GA_EV", "ev": 0}), hi=200)
        f += timed(plc, "GET_MACHINE_STATE", *plc.send({"type": "SYS", "cmd": "GET_MACHINE_STATE"}), hi=200)
        f += timed(plc, "G1 Z12 (behind the wait)", *plc.send({"type": "M", "cmd": "G1", "Z": 12}), hi=200)
        f += timed(plc, "second input wait", *plc.send(dict(DIN_NEVER, timeout_ms=3000)), hi=200, ack=False, err="wait_busy")
        f += timed(plc, "input wait", blk, t0, lo=2900, hi=3300, ack=False, err="block_timeout")

        print("\n2. motion stop wait resolves when the move ends")
        plc.call({"type": "M", "cmd": "G1", "Z": 40})
        f += timed(plc, "WAIT_FOR_MOTION_STOP", *plc.send({"type": "M", "cmd": "WAIT_FOR_MOTION_STOP", "timeout_ms": 10000}),
                   lo=20, hi=8000, ack=True)
        f += timed(plc, "  ... when already stopped", *plc.send({"type": "M", "cmd": "WAIT_FOR_MOTION_STOP"}), hi=200, ack=True)

        print("\n3. reel stop wait does not hold arm moves")
        plc.call({"type": "M", "cmd": "ReelGo", "Distance": 30, "F": 30})
        rw, t0 = plc.send({"type": "M", "cmd": "WAIT_FOR_REEL_STOP", "timeout_ms": 10000})
        time.sleep(0.05)
        f += timed(plc, "G1 Z20 (behind reel wait)", *plc.send({"type": "M", "cmd": "G1", "Z": 20}), hi=200, ack=True)
        f += timed(plc, "WAIT_FOR_REEL_STOP", rw, t0, lo=300, hi=9000, ack=True)

        print("\n4. pending wait when the FSM leaves Ready")
        blk, t0 = plc.send(DIN_NEVER)          # no timeout_ms: 30 s ceiling
        time.sleep(0.3)
        plc.call({"type": "SYS", "cmd": "GA_EV", "ev": 8})
        f += timed(plc, "input wait after EV_RESET", blk, t0, hi=1000, ack=False, err="group_not_ready")

        print("\n5. host goes silent with a motion packet stuck at the queue head")
        to_ready(plc)
        plc.call({"type": "M", "cmd": "SetCoord1"})
        for k in range(14):                    # > MOTION_BUFFER_THRESHOLD (12): the rest wait at the head
            plc.send({"type": "M", "cmd": "G1", "Z": 10 + 5 * (k % 2), "F": 5})
        time.sleep(0.5)
        plc.heartbeat = False
        t0 = time.time()
        time.sleep(6.5)
        plc.heartbeat = True
        r = plc.call({"type": "SYS", "cmd": "GA_EV", "ev": 0}) or {}
        ok = r.get("st_str") == "Error" and r.get("err_src") == "Supervisor:UiHeartbeatStale"
        f += 0 if ok else 1
        print("  %-26s after %.1f s silent: st=%s err_src=%s  %s" % (
            "heartbeat supervisor", time.time() - t0, r.get("st_str"), r.get("err_src"), "ok" if ok else "** FAIL"))
        plc.call({"type": "SYS", "cmd": "GA_EV", "ev": 8})

        print("\nRESULT:", "PASS" if f == 0 else "FAIL (%d)" % f)
    finally:
        plc.close()
    return 1 if f else 0


if __name__ == "__main__":
    sys.exit(main())
