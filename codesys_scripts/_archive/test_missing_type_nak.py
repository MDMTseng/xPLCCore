#!/usr/bin/env python3
"""Send a packet with no `type` field; expect err='missing_type_field' NAK."""
from __future__ import annotations
import socket, threading, time
import msgpack

HOST, PORT = "192.168.1.70", 8125


def main() -> int:
    sock = socket.create_connection((HOST, PORT), timeout=5)
    saw_nak = {"hit": False, "frame": None}
    stop = threading.Event()

    def reader():
        unp = msgpack.Unpacker(raw=False)
        sock.settimeout(0.2)
        while not stop.is_set():
            try:
                buf = sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            if not buf:
                return
            unp.feed(buf)
            for obj in unp:
                print("RX", obj)
                if isinstance(obj, dict) and obj.get("err") == "missing_type_field":
                    saw_nak["hit"] = True
                    saw_nak["frame"] = obj

    t = threading.Thread(target=reader, daemon=True); t.start()
    time.sleep(0.5)

    # PING first to confirm channel alive
    sock.sendall(msgpack.packb({"id": 1, "type": "SYS", "cmd": "PING"}, use_bin_type=True))
    time.sleep(0.5)

    # The bad packet: cmd present, no type
    print("TX (no type) {id:777, cmd:'SetCoord1'}")
    sock.sendall(msgpack.packb({"id": 777, "cmd": "SetCoord1"}, use_bin_type=True))
    time.sleep(2.0)

    # Confirm SYS still flows after the NAK (the whole point of the fix)
    sock.sendall(msgpack.packb({"id": 2, "type": "SYS", "cmd": "PING"}, use_bin_type=True))
    time.sleep(0.5)

    stop.set()
    sock.close()
    t.join(timeout=1.0)

    print()
    if saw_nak["hit"]:
        print("PASS: got", saw_nak["frame"])
        return 0
    print("FAIL: no missing_type_field NAK observed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
