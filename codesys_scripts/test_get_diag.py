#!/usr/bin/env python3
"""Verify SYS/GET_DIAG returns the diagnostic counters bundle."""
from __future__ import annotations
import socket, time
import msgpack

HOST, PORT = "192.168.1.70", 8125

EXPECTED_KEYS = [
    "runtime_ms", "sm_scans",
    "remp_drop", "overlen_drop", "send_stall_drop",
    "group_not_ready_nak", "missing_type_nak",
    "coord_not_cfg_nak", "proto_mismatch_nak",
    "idle_reset", "read_err_reset", "parser_err_reset",
    "ui_ping_count", "ui_hb_stale_count",
    "ping_max_gap_ms", "last_ui_ping_ms",
    "st_chg_event_count",
]

with socket.create_connection((HOST, PORT), timeout=5) as s:
    s.sendall(msgpack.packb({"id": 99, "type": "SYS", "cmd": "PING"}, use_bin_type=True))
    time.sleep(0.3)
    s.sendall(msgpack.packb({"id": 1, "type": "SYS", "cmd": "GET_DIAG"}, use_bin_type=True))
    s.settimeout(2.0)
    unp = msgpack.Unpacker(raw=False)
    deadline = time.time() + 3
    while time.time() < deadline:
        try:
            buf = s.recv(4096)
        except socket.timeout:
            break
        if not buf:
            break
        unp.feed(buf)
        for obj in unp:
            print("RX", obj)
            if isinstance(obj, dict) and obj.get("id") == 1:
                if not obj.get("ack"):
                    print("FAIL: NAK")
                    raise SystemExit(1)
                missing = [k for k in EXPECTED_KEYS if k not in obj]
                if missing:
                    print(f"FAIL: missing keys: {missing}")
                    raise SystemExit(1)
                print()
                print("DIAGNOSTIC COUNTERS")
                for k in EXPECTED_KEYS:
                    print(f"  {k:24s} = {obj[k]}")
                print("PASS")
                raise SystemExit(0)
    print("FAIL: no reply")
    raise SystemExit(1)
