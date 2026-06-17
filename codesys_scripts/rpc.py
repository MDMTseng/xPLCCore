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

    # full push: import_all -> online_change -> pytest regression
    # (this is the default code-push path; --no-tests for hot fixes)
    python rpc.py push
    python rpc.py push --no-tests

    # one-shot environment health check (daemon + UI harness + PLC link)
    python rpc.py status

    # tell the daemon to exit (server-side Ctrl+C also works)
    python rpc.py stop

Exit codes:
    0  success (or ping/stop returned ok)
    1  job ran but raised inside CODESYS (stdout / traceback printed)
    2  daemon not reachable (connection refused)
    3  socket timeout waiting for reply
    4  bad request / bad reply
"""
import sys, json, socket, argparse, os, subprocess

HOST = "127.0.0.1"
PORT = 7420
HERE = os.path.dirname(os.path.abspath(__file__))


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


def do_status(timeout):
    """One-shot environment health check. Reports pass/fail per layer
    so a debugging session starts from "what is actually broken" rather
    than guessing. Returns 0 if all green, 1 otherwise."""
    import json as _json
    rows = []  # (layer, ok, detail)

    # Layer 1: scripting daemon reachable
    try:
        rep = call({"cmd": "ping"}, timeout=min(timeout, 5))
        rows.append(("daemon", bool(rep.get("ok")), "rpc_count=%s" % rep.get("rpc_count", "?")))
    except ConnectionRefusedError:
        rows.append(("daemon", False, "connection refused on %s:%d" % (HOST, PORT)))
    except Exception as ex:
        rows.append(("daemon", False, "error: %s" % ex))

    # Layer 2: UI remote_harness reachable + UI<->PLC link
    # Spawn remote_ctrl.py PING through the UI harness so we exercise the
    # whole chain instead of probing the harness alone.
    try:
        res = subprocess.run(
            [sys.executable, os.path.join(HERE, "remote_ctrl.py"),
             "send_tcp_msgpack", _json.dumps({"type": "SYS", "cmd": "PING"}),
             "--timeout", str(min(timeout, 5))],
            capture_output=True, text=True, timeout=min(timeout, 8),
        )
        out = _json.loads(res.stdout or "{}")
        result = out.get("result") or {}
        inner = result.get("value") if isinstance(result, dict) else None
        if isinstance(inner, dict) and inner.get("pong"):
            rows.append(("ui_harness", True, "pong runtime_ms=%s" % inner.get("runtime_ms", "?")))
        else:
            rows.append(("ui_harness", False, "no pong: %s" % str(out or res.stdout)[:200]))
    except subprocess.TimeoutExpired:
        rows.append(("ui_harness", False, "timeout (UI not running / not connected?)"))
    except Exception as ex:
        rows.append(("ui_harness", False, "error: %s" % ex))

    # Layer 3: PLC FSM state via GET_MACHINE_STATE
    try:
        res = subprocess.run(
            [sys.executable, os.path.join(HERE, "remote_ctrl.py"),
             "send_tcp_msgpack",
             _json.dumps({"type": "SYS", "cmd": "GET_MACHINE_STATE"}),
             "--timeout", str(min(timeout, 5))],
            capture_output=True, text=True, timeout=min(timeout, 8),
        )
        out = _json.loads(res.stdout or "{}")
        result = out.get("result") or {}
        inner = result.get("value") if isinstance(result, dict) else None
        if isinstance(inner, dict) and "st_str" in inner:
            rows.append(("plc_fsm", True, "st=%s coord_set=%s axes_err_mask=%s"
                         % (inner.get("st_str"), inner.get("coord_set"),
                            inner.get("axes_err_mask"))))
        else:
            rows.append(("plc_fsm", False, "no machine-state reply"))
    except Exception as ex:
        rows.append(("plc_fsm", False, "error: %s" % ex))

    width = max(len(r[0]) for r in rows)
    all_ok = True
    for layer, ok, detail in rows:
        mark = "OK " if ok else "FAIL"
        print("[%s] %-*s %s" % (mark, width, layer, detail))
        all_ok = all_ok and ok
    return 0 if all_ok else 1


def main():
    ap = argparse.ArgumentParser(description="CODESYS scripting RPC client")
    ap.add_argument("cmd", choices=["ping", "exec", "stop", "push", "status", "daemon-start"])
    ap.add_argument("--file", help="path to .py file (else read from stdin)")
    ap.add_argument("--label", default="", help="short tag for daemon log")
    ap.add_argument("--timeout", type=float, default=180,
                    help="socket timeout in seconds (default 180)")
    ap.add_argument("--no-tests", action="store_true",
                    help="(push only) skip the pytest regression after online_change")
    args = ap.parse_args()

    if args.cmd == "status":
        sys.exit(do_status(args.timeout))

    if args.cmd == "daemon-start":
        # Hands-off recovery: if the daemon is dead, kickstart it (spawn
        # CODESYS with --runscript=daemon.py if the IDE isn't already up;
        # otherwise print the paste-into-Scripting-Console workaround).
        wrapper = os.path.join(HERE, "daemon_kickstart.py")
        argv = [sys.executable, wrapper, "--wait", str(max(30, args.timeout))]
        sys.exit(subprocess.call(argv))

    if args.cmd == "push":
        # Delegate to the dedicated wrapper so the regression-gated push has
        # one implementation. Anything else (rpc.py ping/exec/stop) keeps
        # talking to the daemon directly.
        wrapper = os.path.join(HERE, "online_change_with_regression.py")
        argv = [sys.executable, wrapper]
        if args.no_tests:
            argv.append("--skip-tests")
        sys.exit(subprocess.call(argv))

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
