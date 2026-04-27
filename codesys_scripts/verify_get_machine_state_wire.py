#!/usr/bin/env python3
"""Sanity-check that GET_MACHINE_STATE now returns 'axes_err_id'."""
import socket, msgpack, sys, time

PLC = ("192.168.1.70", 8125)
sock = socket.create_connection(PLC, timeout=5)
sock.settimeout(1.0)
# Heartbeat first so supervisor doesn't trip Error mid-test.
sock.sendall(msgpack.packb({"id": 9000, "type": "SYS", "cmd": "PING"}, use_bin_type=True))
time.sleep(0.2)
sock.sendall(msgpack.packb({"id": 9001, "type": "SYS", "cmd": "GET_MACHINE_STATE"}, use_bin_type=True))
unp = msgpack.Unpacker(raw=False)
deadline = time.time() + 8.0
while time.time() < deadline:
    try:
        buf = sock.recv(4096)
    except socket.timeout:
        continue
    if not buf:
        break
    unp.feed(buf)
    for obj in unp:
        if isinstance(obj, dict) and obj.get("id") == 9001:
            print("REPLY:", obj)
            print("axes_err_id present:", "axes_err_id" in obj)
            print("axes_err_id value  :", obj.get("axes_err_id"))
            sock.close()
            sys.exit(0 if "axes_err_id" in obj else 1)
print("no reply"); sys.exit(1)
