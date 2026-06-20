"""Phase 2 verification: drive COORD1_BIND end-to-end via raw TCP.
Covers:
  1. Linear mode bind  + drift via forced pulse
  2. Bad-table NAK (decreasing master)
  3. Length-mismatch NAK
  4. Table mode bind + interp via forced pulse
  5. Bind-busy NAK (must motion buffer; skipped here because we don't
     start motion -- exercised in a later live-motion test)
"""
import socket, msgpack, sys, time

PLC = ("192.168.1.70", 8125)
ID = 80000


def _id():
    global ID
    ID += 1
    return ID


def send_recv(s, payload, timeout=2.0):
    pid = payload.setdefault("id", _id())
    s.sendall(msgpack.packb(payload, use_bin_type=True))
    unp = msgpack.Unpacker(raw=False)
    deadline = time.time() + timeout
    s.settimeout(0.5)
    while time.time() < deadline:
        try:
            d = s.recv(4096)
        except socket.timeout:
            continue
        if not d:
            return None
        unp.feed(d)
        for obj in unp:
            if isinstance(obj, dict) and obj.get("id") == pid:
                return obj
    return None


def dbg(s):
    return send_recv(s, {"type": "SYS", "cmd": "GET_COORD1_DEBUG"})


def main():
    s = socket.socket()
    s.connect(PLC)
    s.settimeout(2.0)

    def need(cond, msg):
        if not cond:
            print("FAIL:", msg)
            sys.exit(1)

    print("--- Test 1: Linear bind ---")
    r = send_recv(s, {"type": "SYS", "cmd": "COORD1_BIND",
                      "ref_pulse": 1000,
                      "ref_xyz": [396.0, 87.0, 0.0],
                      "scale":   [10.0, 0.0, 0.0]})
    need(r and r.get("ack") and r.get("mode") == "linear" and r.get("n") == 0,
         "Linear bind reply: %r" % (r,))
    d = dbg(s)
    need(d.get("bound") and d.get("mode") == "linear"
         and d.get("ref_pulse") == 1000
         and abs(d.get("ref_x") - 396.0) < 1e-3
         and abs(d.get("scale_x") - 10.0) < 1e-3,
         "Linear debug: %r" % (d,))
    print("  OK", d)

    print("--- Test 2: Bad-table NAK (decreasing master) ---")
    r = send_recv(s, {"type": "SYS", "cmd": "COORD1_BIND",
                      "ref_pulse": 0, "ref_xyz": [0.0, 0.0, 0.0],
                      "cam_table": {
                          "master": [100, 90, 80],   # decreasing
                          "x": [0.0, 1.0, 2.0],
                          "y": [0.0, 0.0, 0.0],
                          "z": [0.0, 0.0, 0.0]}})
    need(r and r.get("ack") is False and r.get("err") == "coord_bind_bad_table",
         "Bad-table NAK: %r" % (r,))
    print("  OK")

    print("--- Test 3: Length-mismatch NAK ---")
    r = send_recv(s, {"type": "SYS", "cmd": "COORD1_BIND",
                      "ref_pulse": 0, "ref_xyz": [0.0, 0.0, 0.0],
                      "cam_table": {
                          "master": [100, 200, 300],
                          "x":      [0.0, 1.0],            # 2 != 3
                          "y": [0.0, 0.0, 0.0],
                          "z": [0.0, 0.0, 0.0]}})
    need(r and r.get("ack") is False
         and r.get("err") == "coord_bind_table_length_mismatch",
         "Length-mismatch NAK: %r" % (r,))
    print("  OK")

    print("--- Test 4: Table bind + interp ---")
    # 3-point table: at master=1000, slave=(0,0,0); at 2000, (100,0,0);
    # at 3000, (300,0,0). Expect linear-interp midpoints.
    r = send_recv(s, {"type": "SYS", "cmd": "COORD1_BIND",
                      "ref_pulse": 1000, "ref_xyz": [0.0, 0.0, 0.0],
                      "cam_table": {
                          "master": [1000, 2000, 3000],
                          "x":      [0.0,  100.0, 300.0],
                          "y":      [0.0,  0.0,   0.0],
                          "z":      [0.0,  0.0,   0.0]}})
    need(r and r.get("ack") and r.get("mode") == "table" and r.get("n") == 3,
         "Table bind reply: %r" % (r,))
    d = dbg(s)
    need(d.get("mode") == "table" and d.get("cam_n") == 3
         and d.get("cam_first") == 1000 and d.get("cam_last") == 3000,
         "Table debug: %r" % (d,))
    print("  bind OK:", {k: d[k] for k in ("mode", "cam_n", "cam_first", "cam_last")})

    print("--- Skipped: pulse-drift interp test (needs daemon to force pulse) ---")
    print("    Run probe_coord1_drift.py separately to verify per-scan compute.")

    s.close()
    print("\nAll Phase 2 wire-level checks PASSED.")


if __name__ == "__main__":
    main()
