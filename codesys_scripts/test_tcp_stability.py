#!/usr/bin/env python3
"""Verify TCP server idle-reset path.

1. Open connection, send PING, receive pong (warm path).
2. Hold the socket open without sending anything for 8s while another
   thread tries to open a SECOND connection -- under the old code it
   would block forever (max=1). Under the new code, the PLC drops the
   stale socket at 7s and the second connection succeeds.
"""
import socket, struct, threading, time, msgpack, sys

HOST, PORT = "192.168.1.70", 8125


def send_ping(s, ping_id):
    s.sendall(msgpack.packb({"id": ping_id, "type": "SYS", "cmd": "PING"}, use_bin_type=True))


def recv_some(s, timeout=2.0):
    s.settimeout(timeout)
    unp = msgpack.Unpacker(raw=False)
    deadline = time.time() + timeout
    out = []
    while time.time() < deadline:
        try:
            buf = s.recv(4096)
        except socket.timeout:
            break
        if not buf:
            break
        unp.feed(buf)
        for o in unp:
            out.append(o)
    return out


print("[step 1] connect socket A, send PING")
sa = socket.create_connection((HOST, PORT), timeout=5)
send_ping(sa, 1)
print("  reply:", recv_some(sa, 1.0))

# Use SO_LINGER with l_onoff=1, l_linger=0 to send RST on close — this
# is closer to "host crashed" than a normal FIN. But we want the OPPOSITE
# scenario: leave the socket open, drop nothing, and stop talking.
# (Cable yank simulation: the PLC sees no FIN, no RST, just silence.)
print("[step 2] hold socket A silent (no FIN/RST), try to open socket B every 1s")
result = {"connected_at": None, "attempts": []}
stop = threading.Event()


def hammer():
    t0 = time.time()
    while not stop.is_set():
        elapsed = time.time() - t0
        try:
            sb = socket.create_connection((HOST, PORT), timeout=1.5)
            result["connected_at"] = elapsed
            print(f"  [{elapsed:5.2f}s] socket B connected!")
            send_ping(sb, 9999)
            print("    reply on B:", recv_some(sb, 1.5))
            sb.close()
            return
        except OSError as ex:
            result["attempts"].append((elapsed, str(ex)[:60]))
        time.sleep(1.0)


t = threading.Thread(target=hammer, daemon=True)
t.start()

# Block for 12s while we silently hold socket A.
time.sleep(12.0)
stop.set()
t.join(timeout=2.0)

print("[step 3] close socket A (already idle-reset by now, this is just cleanup)")
try:
    sa.close()
except OSError:
    pass

print("\n=== SUMMARY ===")
print("attempts before B succeeded:", len(result["attempts"]))
for a in result["attempts"]:
    print(f"  t={a[0]:5.2f}s err={a[1]}")
if result["connected_at"] is not None:
    print(f"socket B connected at t={result['connected_at']:.2f}s")
    print("PASS - PLC reclaimed slot")
    sys.exit(0)
else:
    print("FAIL - socket B never connected within window")
    sys.exit(1)
