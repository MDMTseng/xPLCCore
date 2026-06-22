#!/usr/bin/env python3
"""client_takeover.py -- canonical W1 graceful client handoff.

Background. PLC TCP 8125 accepts ONE client. The renderer normally owns
it. To run tooling/tests/probes that need direct PLC access without
fighting the UI, we ask the UI to release the socket cleanly, then
connect directly, then (optionally) hand control back.

This is the W1 graceful-transfer mechanism. Two CLI verbs:

  python client_takeover.py acquire [--timeout S]
  python client_takeover.py release [--timeout S]

`acquire` posts disconnect_tcp through remote_ctrl, waits for the UI to
release, opens a fresh direct socket, does a SYS/PING smoke test, then
emits the PING reply on stdout (JSON) and exits 0.

`release` does the reverse: closes any held socket (irrelevant; the
script doesn't keep state) and posts connect_tcp through remote_ctrl so
the UI reconnects.

Most scripts don't need this directly -- they use the raw_plc_socket
pytest fixture which already wraps acquire/release. This standalone is
for ad-hoc work and for embedding in CI / non-pytest tooling.

Per decisions_2026-06-22.md C.10: the W1 design currently relies on
voluntary cooperation from the UI (HTTP remote-harness on 127.0.0.1:8127).
If the UI is hung / unreachable, there is no PLC-side forced takeover
yet -- the operator must manually disconnect the UI's socket. That's
acceptable because A3 heartbeat supervisor will trip the FSM to Error
within 5s of UI silence anyway, freeing the socket once it processes the
trip.
"""
import argparse
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REMOTE_CTRL = HERE / "remote_ctrl.py"
PLC_HOST, PLC_PORT = "192.168.1.70", 8125

try:
    import msgpack
except ImportError:
    print("client_takeover requires msgpack. pip install msgpack", file=sys.stderr)
    sys.exit(2)


def _post_remote(action: str, payload: str, timeout: float) -> int:
    """Drive remote_ctrl. Returns the subprocess exit code."""
    cp = subprocess.run(
        [sys.executable, str(REMOTE_CTRL), action, payload,
         "--timeout", str(timeout)],
        capture_output=True, text=True, timeout=timeout + 3,
    )
    if cp.returncode != 0:
        print(f"remote_ctrl {action} exit={cp.returncode}", file=sys.stderr)
        if cp.stderr:
            print(cp.stderr, file=sys.stderr)
    return cp.returncode


def _try_ping(timeout: float = 2.0) -> dict | None:
    """Direct PLC connect + SYS/PING. Returns the reply dict or None."""
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((PLC_HOST, PLC_PORT))
        s.sendall(msgpack.packb({"type": "SYS", "cmd": "PING", "id": 1},
                                use_bin_type=True))
        unp = msgpack.Unpacker(raw=False, strict_map_key=False)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                chunk = s.recv(4096)
            except socket.timeout:
                continue
            if not chunk:
                return None
            unp.feed(chunk)
            for obj in unp:
                if isinstance(obj, dict) and obj.get("id") == 1:
                    return obj
        return None
    except OSError as e:
        return {"err": str(e)}
    finally:
        try: s.close()
        except OSError: pass


def acquire(timeout: float) -> int:
    rc = _post_remote("disconnect_tcp", "{}", timeout=timeout)
    if rc != 0:
        # Non-fatal: the UI may not be running. Try connecting directly
        # anyway -- if the socket's free, we win.
        print("note: remote_ctrl disconnect failed; trying direct connect anyway",
              file=sys.stderr)
    # Give the UI a beat to actually close its socket.
    time.sleep(0.5)
    reply = _try_ping()
    if reply is None:
        print("FAILED: connected but no PING reply (PLC may be unhealthy)",
              file=sys.stderr)
        return 1
    if "err" in reply:
        print(f"FAILED: {reply['err']}", file=sys.stderr)
        return 1
    print(json.dumps(reply, default=str))
    return 0


def release(timeout: float) -> int:
    payload = json.dumps({"host": PLC_HOST, "port": PLC_PORT})
    rc = _post_remote("connect_tcp", payload, timeout=timeout)
    if rc != 0:
        return rc
    # Give the UI's reconnect a beat. It should fetch GET_MACHINE_STATE
    # automatically on tcpConnected false->true.
    time.sleep(0.5)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("verb", choices=("acquire", "release"))
    ap.add_argument("--timeout", type=float, default=5.0)
    args = ap.parse_args()
    return acquire(args.timeout) if args.verb == "acquire" else release(args.timeout)


if __name__ == "__main__":
    sys.exit(main())
