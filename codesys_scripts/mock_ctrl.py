#!/usr/bin/env python3
"""
mock_ctrl.py  <cmd-line>

Fire a single control command at the running mock_plc ctrl port and print
the response. Example:

  python codesys_scripts/mock_ctrl.py status
  python codesys_scripts/mock_ctrl.py drop 3
  python codesys_scripts/mock_ctrl.py nak "synthetic failure"
  python codesys_scripts/mock_ctrl.py disconnect
"""
import socket
import sys

HOST = "127.0.0.1"
PORT = 8126


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: mock_ctrl.py <cmd> [args...]", file=sys.stderr)
        return 2
    line = " ".join(sys.argv[1:]).strip() + "\n"
    s = socket.create_connection((HOST, PORT), timeout=2.0)
    s.sendall(line.encode("utf-8"))
    # Read banner + response; stop when we see the second prompt.
    buf = b""
    s.settimeout(1.0)
    try:
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
            if buf.count(b"> ") >= 2:
                break
    except socket.timeout:
        pass
    finally:
        s.close()
    # Trim banner (before first "> ") and trailing prompt.
    text = buf.decode("utf-8", "replace")
    lines = [ln for ln in text.splitlines() if ln and not ln.startswith("mock_plc ready") and ln.strip() != ">"]
    for ln in lines:
        # Drop the "> " prefix if present.
        if ln.startswith("> "):
            ln = ln[2:]
        print(ln)
    return 0


if __name__ == "__main__":
    sys.exit(main())
