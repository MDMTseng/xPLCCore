#!/usr/bin/env python3
"""
tcp_client.py  <template-name>

Reads codesys_scripts/jobs/templates/<name>.py, sends it to the warm-session
TCP server running inside CODESYS, and streams the response.

Exit code follows the `__DONE__ rc=<n>` line from the server. If no __DONE__
line is seen the exit code is 1.
"""
import os
import sys
import socket

# Force UTF-8 output — otherwise Windows cp950 / cp1252 blows up on
# non-ASCII bytes in CODESYS log output (e.g. curly quotes, em-dashes).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass  # older Python; best-effort

HOST = "127.0.0.1"
PORT = 9790
EOF  = b"\n__EOF__\n"
HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    if len(sys.argv) < 2:
        print("usage: tcp_client.py <template-name>", file=sys.stderr)
        return 2
    name = sys.argv[1]
    path = os.path.join(HERE, "jobs", "templates", name + ".py")
    if not os.path.isfile(path):
        print(f"no template: {path}", file=sys.stderr)
        return 2

    with open(path, "rb") as f:
        body = f.read()

    try:
        s = socket.create_connection((HOST, PORT), timeout=5.0)
    except OSError as ex:
        print(f"connect to {HOST}:{PORT} failed: {ex}", file=sys.stderr)
        print("Is tcp_server.py running inside CODESYS?", file=sys.stderr)
        return 2

    s.settimeout(None)
    s.sendall(body)
    s.sendall(EOF)

    # Stream response; detect __DONE__ line for exit code.
    rc = 1
    pending = b""
    done_seen = False
    try:
        while not done_seen:
            chunk = s.recv(4096)
            if not chunk:
                break
            pending += chunk
            while b"\n" in pending:
                nl = pending.index(b"\n")
                line = pending[:nl]
                pending = pending[nl+1:]
                text = line.decode("utf-8", "replace")
                if text.startswith("__DONE__ rc="):
                    try:
                        rc = int(text.split("=", 1)[1].strip())
                    except ValueError:
                        rc = 1
                    done_seen = True
                    break
                sys.stdout.write(text + "\n")
                sys.stdout.flush()
    finally:
        try:
            s.close()
        except Exception:
            pass
    return rc


if __name__ == "__main__":
    sys.exit(main())
