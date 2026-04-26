#!/usr/bin/env python3
"""rpc.py -- thin client for the CODESYS scripting daemon.

Replaces the file-based watcher submission flow. The daemon is a long-running
script in the CODESYS Scripting Console (codesys_scripts/daemon.py).

Usage:
    # ping the daemon (also doubles as a liveness probe)
    python rpc.py ping

    # run a script file in the warm CODESYS session
    python rpc.py exec --file my_job.py

    # run a snippet from stdin
    echo 'print(projects.primary.path)' | python rpc.py exec --label whereami

    # tell the daemon to exit (server-side Ctrl+C also works)
    python rpc.py stop

Exit codes:
    0  success (or ping/stop returned ok)
    1  job ran but raised inside CODESYS (stdout / traceback printed)
    2  daemon not reachable (connection refused)
    3  socket timeout waiting for reply
    4  bad request / bad reply
"""
import sys, json, socket, argparse, os

HOST = "127.0.0.1"
PORT = 7420


def call(req, timeout):
    s = socket.create_connection((HOST, PORT), timeout=timeout)
    try:
        s.sendall((json.dumps(req) + "\n").encode("utf-8"))
        # NOTE: do NOT shutdown(SHUT_WR) here. The IronPython socket inside
        # CODESYS wedges on a half-closed peer; the daemon relies on the '\n'
        # line delimiter to know the request is complete.
        # The daemon's reply is one '\n'-terminated JSON line. Break as soon
        # as the line is complete -- don't wait for EOF, because IronPython's
        # sock.close() inside CODESYS doesn't reliably deliver FIN, which
        # leaves recv() hanging until our socket timeout. (Smoke test
        # 2026-04-25 surfaced this on set_force_value-bearing jobs.)
        chunks = []
        while True:
            buf = s.recv(65536)
            if not buf:
                break
            chunks.append(buf)
            if b"\n" in buf:
                break
    finally:
        s.close()
    raw = b"".join(chunks).decode("utf-8", "replace").strip()
    if not raw:
        raise ValueError("empty reply from daemon")
    return json.loads(raw)


def main():
    ap = argparse.ArgumentParser(description="CODESYS scripting RPC client")
    ap.add_argument("cmd", choices=["ping", "exec", "stop"])
    ap.add_argument("--file", help="path to .py file (else read from stdin)")
    ap.add_argument("--label", default="", help="short tag for daemon log")
    ap.add_argument("--timeout", type=float, default=180,
                    help="socket timeout in seconds (default 180)")
    args = ap.parse_args()

    req = {"cmd": args.cmd}
    if args.cmd == "exec":
        if args.file:
            with open(args.file, "r", encoding="utf-8") as f:
                code = f.read()
            label = args.label or os.path.basename(args.file)
        else:
            if sys.stdin.isatty():
                print("ERROR: no --file and stdin is a tty", file=sys.stderr)
                sys.exit(4)
            code = sys.stdin.read()
            label = args.label or "stdin"
        req["code"]  = code
        req["label"] = label

    try:
        rep = call(req, timeout=args.timeout)
    except ConnectionRefusedError:
        print("ERROR: connection refused on %s:%d -- is daemon.py running in the CODESYS Scripting Console?"
              % (HOST, PORT), file=sys.stderr)
        sys.exit(2)
    except socket.timeout:
        print("ERROR: socket timeout after %ss waiting for daemon reply"
              % args.timeout, file=sys.stderr)
        sys.exit(3)
    except (ValueError, json.JSONDecodeError) as ex:
        print("ERROR: bad reply: %s" % ex, file=sys.stderr)
        sys.exit(4)

    if args.cmd == "exec":
        sys.stdout.write(rep.get("stdout", ""))
        if not rep.get("stdout", "").endswith("\n"):
            sys.stdout.write("\n")
        elapsed = rep.get("elapsed", 0.0)
        if rep.get("ok"):
            sys.stderr.write("[rpc] ok elapsed=%.2fs rpc_count=%s\n"
                             % (elapsed, rep.get("rpc_count", "?")))
            sys.exit(0)
        else:
            sys.stderr.write("[rpc] FAIL elapsed=%.2fs error=%s\n"
                             % (elapsed, (rep.get("error") or "(none)").splitlines()[0]
                                if rep.get("error") else "(none)"))
            sys.exit(1)

    # ping / stop
    print(json.dumps(rep, indent=2))
    sys.exit(0 if rep.get("ok") else 1)


if __name__ == "__main__":
    main()
