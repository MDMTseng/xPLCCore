"""Host->PLC queue behaviour test (review 2026-09-24 P0-3), direct TCP.

    python tools/sim/queue_test.py [--plc 192.168.1.70]

Talks msgpack to PLC :8125 itself, so the UI must NOT be connected (the
PLC takes one client). Refuses unless the delta arms are virtual (read
through the CODESYS daemon). Moves: only small Z moves of the (virtual)
delta group; EAXIS_A is never commanded.

Scenario "wait at the motion head": a BLOCK_FOR_DIGITAL_INPUT that can
never be satisfied (timeout 3 s) is sent, then PING, GA_EV(0),
GET_MACHINE_STATE and a G1 right behind it. Order-free SYS commands must
reply at once; the G1 is reported so the motion behaviour is visible.
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
    fails = 0
    try:
        to_ready(plc)
        print("Ready; SetCoord1 ->", plc.call({"type": "M", "cmd": "SetCoord1"}))
        print("G1 Z10 ->", plc.call({"type": "M", "cmd": "G1", "Z": 10}))

        print("\n-- wait at the motion head --")
        blk, t0 = plc.send({"type": "M", "cmd": "BLOCK_FOR_DIGITAL_INPUT",
                            "pin": 0x80, "group": 0, "state": 0x80, "timeout_ms": 3000})
        time.sleep(0.05)
        probes = [("PING", {"type": "SYS", "cmd": "PING"}, 0.2),
                  ("GA_EV(0)", {"type": "SYS", "cmd": "GA_EV", "ev": 0}, 0.2),
                  ("GET_MACHINE_STATE", {"type": "SYS", "cmd": "GET_MACHINE_STATE"}, 0.2),
                  ("G1 Z12", {"type": "M", "cmd": "G1", "Z": 12}, None)]
        sent = [(name, limit) + plc.send(pkt) for name, pkt, limit in probes]
        t_blk, r_blk = plc.wait(blk, 8)
        for name, limit, i, ts in sent:
            t_rx, msg = plc.wait(i, 8)
            if t_rx is None:
                print("  %-18s NO REPLY" % name)
                fails += 1
                continue
            dt = t_rx - ts
            ok = limit is None or dt <= limit
            fails += 0 if ok else 1
            print("  %-18s %6.0f ms  ack=%s  %s" % (name, dt * 1000, msg.get("ack"),
                                                   "" if ok else "** TOO SLOW (limit %d ms)" % (limit * 1000)))
        print("  %-18s %6.0f ms  ack=%s err=%s" % ("BLOCK (3 s)", ((t_blk or 0) - t0) * 1000,
                                                  (r_blk or {}).get("ack"), (r_blk or {}).get("err")))
        print("\nRESULT:", "PASS" if fails == 0 else "FAIL (%d)" % fails)
    finally:
        plc.close()
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
