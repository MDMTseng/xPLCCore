#!/usr/bin/env python3
"""Verify GET_MACHINE_STATE now returns axes_err_mask + axes_state."""
from __future__ import annotations
import socket, time
import msgpack

HOST, PORT = "192.168.1.70", 8125

with socket.create_connection((HOST, PORT), timeout=5) as s:
    # warm-up PING -- proves channel alive, also resets idle watchdog
    s.sendall(msgpack.packb({"id": 99, "type": "SYS", "cmd": "PING"}, use_bin_type=True))
    time.sleep(0.3)
    s.sendall(msgpack.packb({"id": 1, "type": "SYS", "cmd": "GET_MACHINE_STATE"}, use_bin_type=True))
    s.settimeout(2.0)
    unp = msgpack.Unpacker(raw=False)
    deadline = time.time() + 3
    raw = bytearray()
    while time.time() < deadline:
        try:
            buf = s.recv(4096)
        except socket.timeout:
            break
        if not buf:
            break
        raw.extend(buf)
        print(f"[raw {len(buf)}B] {buf.hex()}")
        unp.feed(buf)
        for obj in unp:
            print("RX", obj)
            if isinstance(obj, dict) and obj.get("id") == 1:
                mask = obj.get("axes_err_mask")
                packed = obj.get("axes_state")
                if mask is None or packed is None:
                    print("FAIL: missing axes_err_mask or axes_state")
                    raise SystemExit(1)
                states = [(packed >> (8*i)) & 0xFF for i in range(4)]
                names = {0:"power_off",1:"errorstop",2:"stopping",3:"standstill",
                         4:"discrete_motion",5:"continuous_motion",6:"synchronized_motion",
                         7:"homing",8:"prepare_homing"}
                print()
                print(f"axes_err_mask = 0x{mask:01x}  bits=[axis0={mask&1}, axis1={(mask>>1)&1}, axis2={(mask>>2)&1}, reel={(mask>>3)&1}]")
                print(f"axes_state    = 0x{packed:08x}")
                for i, label in enumerate(["EAxis0", "EAxis1", "EAxis2", "reel"]):
                    s_v = states[i]
                    print(f"  {label:6s} state={s_v:3d} ({names.get(s_v, '?')})")
                print("PASS")
                raise SystemExit(0)
    print("FAIL: no reply")
    raise SystemExit(1)
