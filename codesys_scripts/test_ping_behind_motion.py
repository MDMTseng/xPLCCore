#!/usr/bin/env python3
"""Verify PING queued behind motion is no longer false-NAK'd as group_not_ready.

Setup: drive FSM to UnInited (not-Ready) via EV_RESET. Then in a single burst
send a motion-shaped packet followed by a PING. Expect:
  - motion -> err='group_not_ready'  (correct: FSM not Ready)
  - PING   -> pong=true              (correct: SYS dispatched, not blanket-NAKed)

Pre-fix: PING would also come back with err='group_not_ready'.
"""
import socket, time, msgpack

HOST, PORT = "192.168.1.70", 8125
sock = socket.create_connection((HOST, PORT), timeout=5)
unp = msgpack.Unpacker(raw=False)
sock.settimeout(2.0)


def collect_ids(ids, timeout=2.0):
    deadline = time.time() + timeout
    out = {}
    while time.time() < deadline and len(out) < len(ids):
        try:
            buf = sock.recv(4096)
        except socket.timeout:
            break
        if not buf:
            break
        unp.feed(buf)
        for obj in unp:
            if obj.get("id") in ids:
                out[obj["id"]] = obj
    return out


# Step 1: reset to UnInited (event 8 = EV_RESET).
sock.sendall(msgpack.packb({"id": 5001, "type": "SYS", "cmd": "GA_EV", "ev": 8}, use_bin_type=True))
time.sleep(0.4)
collect_ids({5001}, 1.0)

# Step 2: get_machine_state to confirm not-Ready
sock.sendall(msgpack.packb({"id": 5002, "type": "SYS", "cmd": "GET_MACHINE_STATE"}, use_bin_type=True))
state_reply = collect_ids({5002}, 1.5).get(5002)
print("FSM state after reset:", state_reply)

# Step 3: SINGLE burst -- motion packet, then PING.
motion_pkt = {"id": 5003, "type": "M", "cmd": "G1", "x": 0, "y": 0, "z": 0, "f": 100}
ping_pkt   = {"id": 5004, "type": "SYS", "cmd": "PING"}
burst = msgpack.packb(motion_pkt, use_bin_type=True) + msgpack.packb(ping_pkt, use_bin_type=True)
sock.sendall(burst)

replies = collect_ids({5003, 5004}, 2.0)
print("motion reply:", replies.get(5003))
print("ping   reply:", replies.get(5004))

motion = replies.get(5003) or {}
ping   = replies.get(5004) or {}

ok = (motion.get("err") == "group_not_ready") and (ping.get("pong") is True)
print()
if ok:
    print("PASS - PING dispatched correctly while motion was group_not_ready'd")
else:
    print("FAIL")
    print("  motion.err expected 'group_not_ready', got:", motion.get("err"))
    print("  ping.pong   expected True,             got:", ping.get("pong"),
          "  err=", ping.get("err"))
sock.close()
