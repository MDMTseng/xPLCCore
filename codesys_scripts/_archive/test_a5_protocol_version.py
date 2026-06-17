#!/usr/bin/env python3
"""Verify W1 A5 protocol_version gate.

Sends three packets:
  1. PING with no protocol_version (legacy) -> expect pong
  2. PING with protocol_version=1 (correct) -> expect pong
  3. PING with protocol_version=99 (wrong) -> expect protocol_version_mismatch NAK
"""
import socket, time, msgpack

HOST, PORT = "192.168.1.70", 8125
sock = socket.create_connection((HOST, PORT), timeout=5)
unp = msgpack.Unpacker(raw=False)
sock.settimeout(2.0)


def rt(pkt):
    sock.sendall(msgpack.packb(pkt, use_bin_type=True))
    deadline = time.time() + 2.0
    while time.time() < deadline:
        try:
            buf = sock.recv(4096)
        except socket.timeout:
            break
        if not buf:
            break
        unp.feed(buf)
        for obj in unp:
            if obj.get("id") == pkt["id"]:
                return obj
    return None


cases = [
    ("legacy (no field)",   {"id": 1001, "type": "SYS", "cmd": "PING"}),
    ("correct (version=1)", {"id": 1002, "type": "SYS", "cmd": "PING", "protocol_version": 1}),
    ("wrong (version=99)",  {"id": 1003, "type": "SYS", "cmd": "PING", "protocol_version": 99}),
]
for label, pkt in cases:
    reply = rt(pkt)
    print(f"{label:25s} -> {reply}")

sock.close()
