#!/usr/bin/env python3
"""rpc.py -- client for the CODESYS scripting daemon (v2).

The daemon is a long-lived IronPython process inside the CODESYS
Scripting Console (daemon.py). This is its terminal-side client.

What changed from v1
--------------------
v1 exposed ping / exec / stop. Anything else -- saving, checking a PLC
variable, poking at the IDE -- meant stopping the daemon, and stopping
was the operation that hung. So the workflow forced you through the
dangerous path several times a day.

v2 serves the common reasons you used to stop:

    rpc.py save                 persist the project without leaving
    rpc.py read GVL.Foo         read a symbol through the live session
    rpc.py write GVL.Foo TRUE   write (--force to force)
    rpc.py logout               drop the online session, keep the daemon

and makes leaving a real operation with visible progress:

    rpc.py yield                hand the IDE back, wait, report the phase

`yield` polls daemon.status while the daemon tears down, so instead of
staring at a frozen IDE you see which step is slow:

    release:logout    12.4s
    release:save       0.3s
    stopped

Usage
-----
    rpc.py ping | status
    rpc.py exec --file job.py [--readonly] [--no-snapshot]
    echo 'print(projects.primary.path)' | rpc.py exec --label whereami
    rpc.py save | snapshot | logout
    rpc.py read  GVL.AxisGroupSMScans
    rpc.py write GVL.bVirtualMotorsMode_Request TRUE [--force]
    rpc.py yield [--timeout 120]
    rpc.py stop
    rpc.py doctor

Exit codes
----------
    0  success
    1  job ran but raised inside CODESYS (stdout / traceback printed)
    2  daemon not reachable (connection refused)
    3  socket timeout waiting for reply
    4  bad request / bad reply
    5  daemon wedged during release (see the reported phase)
"""

import sys
import os
import json
import socket
import argparse
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import config
import layout_check

E_OK, E_JOB, E_REFUSED, E_TIMEOUT, E_PROTO, E_WEDGED = 0, 1, 2, 3, 4, 5


def status_file():
    return os.path.join(config.state_dir(), "daemon.status")


def read_status_file():
    """The heartbeat file is written by a side thread, so it keeps
    updating even when the daemon's main thread is blocked inside a
    CODESYS call. That makes it the only reliable way to observe a
    wedged daemon -- RPC will not answer, but this still ticks."""
    path = status_file()
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            line = f.read().strip()
    except OSError:
        return None
    if not line:
        return None
    out = {"_raw": line, "_mtime": os.path.getmtime(path)}
    for token in line.split():
        if "=" in token:
            k, v = token.split("=", 1)
            out[k] = v
    return out


def call(req, timeout):
    s = socket.create_connection(
        (config.rpc_host(), config.rpc_port()), timeout=timeout)
    try:
        s.sendall((json.dumps(req) + "\n").encode("utf-8"))
        # Do NOT shutdown(SHUT_WR) here. The IronPython socket inside
        # CODESYS wedges on a half-closed peer; the daemon relies on the
        # '\n' delimiter to know the request is complete.
        #
        # Break as soon as the line is complete rather than waiting for
        # EOF: IronPython's sock.close() inside CODESYS does not reliably
        # deliver FIN, which leaves recv() hanging until our own timeout.
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


def call_or_exit(req, timeout):
    try:
        return call(req, timeout)
    except ConnectionRefusedError:
        hint = read_status_file()
        print("daemon not reachable on %s:%d"
              % (config.rpc_host(), config.rpc_port()), file=sys.stderr)
        if hint:
            age = time.time() - hint["_mtime"]
            print("last heartbeat %.0fs ago: %s" % (age, hint["_raw"]),
                  file=sys.stderr)
        print("start it with: rpc.py daemon-start", file=sys.stderr)
        sys.exit(E_REFUSED)
    except socket.timeout:
        print("timed out waiting for the daemon", file=sys.stderr)
        hint = read_status_file()
        if hint:
            print("heartbeat says: %s" % hint["_raw"], file=sys.stderr)
        sys.exit(E_TIMEOUT)
    except (ValueError, json.JSONDecodeError) as ex:
        print("bad reply: %s" % ex, file=sys.stderr)
        sys.exit(E_PROTO)


# ---- commands --------------------------------------------------------

def cmd_ping(args):
    rep = call_or_exit({"cmd": "ping"}, args.timeout)
    width = max(len(k) for k in rep)
    for k, v in rep.items():
        print("  %-*s %s" % (width, k, v))
    return E_OK if rep.get("ok") else E_JOB


def cmd_save(args):
    rep = call_or_exit({"cmd": "save"}, args.timeout)
    print(rep.get("detail") or rep)
    return E_OK if rep.get("ok") else E_JOB


def cmd_snapshot(args):
    rep = call_or_exit({"cmd": "snapshot", "label": args.label},
                       args.timeout)
    print(rep.get("snapshot") or rep)
    return E_OK if rep.get("ok") else E_JOB


def cmd_logout(args):
    rep = call_or_exit({"cmd": "logout"}, args.timeout)
    print(rep.get("detail") or rep)
    return E_OK if rep.get("ok") else E_JOB


def cmd_read(args):
    rep = call_or_exit({"cmd": "read", "symbol": args.symbol}, args.timeout)
    if rep.get("ok"):
        print(rep.get("value"))
        return E_OK
    print(rep.get("error"), file=sys.stderr)
    if args.verbose and rep.get("traceback"):
        print(rep["traceback"], file=sys.stderr)
    return E_JOB


def cmd_write(args):
    rep = call_or_exit({"cmd": "write", "symbol": args.symbol,
                        "value": args.value, "force": args.force},
                       args.timeout)
    if rep.get("ok"):
        print("%s = %s%s" % (rep.get("symbol"), rep.get("value"),
                             " (forced)" if rep.get("forced") else ""))
        return E_OK
    print(rep.get("error"), file=sys.stderr)
    if args.verbose and rep.get("traceback"):
        print(rep["traceback"], file=sys.stderr)
    return E_JOB


def cmd_exec(args):
    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            code = f.read()
        label = args.label or os.path.basename(args.file)
    else:
        code = sys.stdin.read()
        label = args.label or "stdin"
    rep = call_or_exit({"cmd": "exec", "code": code, "label": label,
                        "readonly": args.readonly,
                        "snapshot": not args.no_snapshot,
                        "plc": args.plc},
                       args.timeout)
    out = rep.get("stdout") or ""
    if out:
        print(out, end="" if out.endswith("\n") else "\n")
    if rep.get("snapshot"):
        print("[snapshot] %s" % rep["snapshot"])
    if not args.readonly:
        print("[saved] %s" % rep.get("saved"))
        if rep.get("save_detail"):
            print("[save]  %s" % rep["save_detail"], file=sys.stderr)
    if rep.get("budget_exceeded"):
        print("[budget] session retiring: %s" % rep["budget_exceeded"])
    if not rep.get("ok"):
        if rep.get("error"):
            print(rep["error"], file=sys.stderr)
        return E_JOB
    return E_OK


def _wait_for_release(timeout, quiet=False):
    """Watch the heartbeat file while the daemon tears down.

    This is the whole point of v2's release path: the phases published
    during teardown turn 'CODESYS is frozen and I do not know why' into
    'it is sitting in release:logout'."""
    deadline = time.time() + timeout
    last_phase = None
    phase_started = time.time()
    while time.time() < deadline:
        snap = read_status_file()
        if snap is None:
            time.sleep(0.4)
            continue
        phase = snap.get("phase", "?")
        if phase != last_phase:
            if last_phase is not None and not quiet:
                print("  %-18s %.1fs" % (last_phase, time.time() - phase_started))
            last_phase = phase
            phase_started = time.time()
        if phase == "stopped":
            if not quiet:
                print("  %-18s done" % phase)
            return True, phase, snap
        time.sleep(0.4)
    return False, last_phase, read_status_file()


def cmd_yield(args):
    cmd = "stop" if args.hard_stop else "yield"
    try:
        rep = call({"cmd": cmd, "reason": args.reason}, args.timeout)
    except ConnectionRefusedError:
        print("daemon already down")
        return E_OK
    except socket.timeout:
        print("daemon did not acknowledge; watching heartbeat anyway",
              file=sys.stderr)
        rep = {}
    if rep and not rep.get("ok"):
        print("daemon refused: %s" % rep, file=sys.stderr)
        return E_PROTO

    print("releasing the IDE (%s)..." % (args.reason or cmd))
    done, phase, snap = _wait_for_release(args.wait)
    if done:
        # Never claim the project was saved without checking. A save can
        # fail without raising -- a dismissed modal reports back as
        # "Operation cancelled by user" -- and the daemon publishes the
        # verdict through the heartbeat because the RPC reply is sent
        # before teardown even starts.
        saved = (snap or {}).get("save", "-")
        if saved == "FAILED":
            print("", file=sys.stderr)
            print("IDE released, but THE SAVE FAILED.", file=sys.stderr)
            print("Your changes are still only in the IDE. Save manually",
                  file=sys.stderr)
            print("(Ctrl+S) before closing CODESYS or killing it.",
                  file=sys.stderr)
            print("See: %s" % os.path.join(config.state_dir(),
                                           "daemon.rpc.log"), file=sys.stderr)
            return E_JOB
        if saved == "ok":
            print("IDE released. CODESYS stays open with the project saved.")
        else:
            print("IDE released. CODESYS stays open.")
            print("(no save was needed)")
        return E_OK
    print("", file=sys.stderr)
    print("STILL WEDGED after %ds, stuck in phase: %s"
          % (args.wait, phase), file=sys.stderr)
    print("", file=sys.stderr)
    print("The project was saved before this phase unless the phase IS",
          file=sys.stderr)
    print("release:save. Check %s" % status_file(), file=sys.stderr)
    print("Recover with: supervisor.py kill  (snapshots are in %s)"
          % config.snapshot_dir(), file=sys.stderr)
    return E_WEDGED


def _exec_template(name, label, timeout, readonly=False, plc="keep"):
    """Run one of jobs/templates/*.py through the daemon."""
    path = os.path.join(HERE, "jobs", "templates", name)
    if not os.path.isfile(path):
        print("missing job template: %s" % path, file=sys.stderr)
        return E_PROTO, {}
    with open(path, "r", encoding="utf-8") as f:
        code = f.read()
    rep = call_or_exit({"cmd": "exec", "code": code, "label": label,
                        "readonly": readonly, "plc": plc}, timeout)
    out = rep.get("stdout") or ""
    if out:
        print(out, end="" if out.endswith("\n") else "\n")
    if not rep.get("ok"):
        if rep.get("error"):
            print(rep["error"], file=sys.stderr)
        return E_JOB, rep
    return E_OK, rep


def cmd_push(args):
    """import .st from disk -> online change -> regression tests.

    Each stage is a daemon transaction, so each one snapshots first and
    saves after. v1 relied on online_change.py happening to call save(),
    which meant a push that stopped after import_all left every edit
    unsaved -- exactly the state that made a later hang expensive."""
    stages = []

    print("== import_all ==")
    rc, rep = _exec_template("import_all.py", "push:import", args.timeout)
    stages.append(("import_all", rc))
    if rep.get("snapshot"):
        print("[snapshot] %s" % rep["snapshot"])
    print("[saved] %s" % rep.get("saved"))
    if rc != E_OK:
        print("push aborted at import_all", file=sys.stderr)
        return rc

    if args.no_online:
        # The regression suite talks to the PLC, so "do not touch the
        # PLC" has to skip it too. It used to run anyway, and one of its
        # jobs logged in with Try over a layout change: that became a
        # download of the running app and wedged the PLC (2026-09-25).
        print("")
        print("--no-online: PLC untouched, regression skipped")
        return E_OK

    print("")
    print("== layout check ==")
    ok, msgs = layout_check.check_disk()
    for m in msgs:
        print("  " + m)
    if not ok:
        print("push refused: this edit changes the memory layout (or the",
              file=sys.stderr)
        print("baseline is missing), so an online change would fall back to",
              file=sys.stderr)
        print("a full download of the running PLC. The project is imported",
              file=sys.stderr)
        print("and saved; install it with someone at the machine:",
              file=sys.stderr)
        print("  rpc.py install --on-site", file=sys.stderr)
        return E_JOB
    print("  no layout change since the last deploy")

    print("")
    print("== online_change ==")
    rc, rep = _exec_template("online_change.py", "push:online",
                             args.timeout, plc="online_change")
    stages.append(("online_change", rc))
    if rc != E_OK:
        print("push aborted at online_change", file=sys.stderr)
        return rc
    print("[layout] baseline: %s" % layout_check.save_baseline(
        layout_check.scan_disk(), "push"))

    if not args.no_tests:
        print("")
        print("== regression ==")
        import subprocess
        res = subprocess.run(
            [sys.executable, "-m", "pytest", os.path.join(HERE, "tests")],
            cwd=os.path.dirname(HERE))
        stages.append(("pytest", res.returncode))
        if res.returncode != 0:
            print("push: tests failed", file=sys.stderr)
            return E_JOB

    print("")
    for name, code in stages:
        print("  %-14s %s" % (name, "ok" if code == 0 else "FAILED(%d)" % code))
    return E_OK


def cmd_layout(args):
    """Show or record the layout baseline used by `push`."""
    if args.baseline_from_git:
        files = layout_check.scan_git(args.baseline_from_git)
        path = layout_check.save_baseline(
            files, "git %s" % args.baseline_from_git)
        print("baseline recorded from git %s: %s"
              % (args.baseline_from_git, path))
        return E_OK
    base = layout_check.load_baseline()
    if base:
        print("baseline: %s (%s)" % (base["recorded"], base["how"]))
    ok, msgs = layout_check.check_disk()
    for m in msgs:
        print("  " + m)
    print("online change OK" if ok else "needs install (stop + download)")
    return E_OK if ok else E_JOB


def cmd_mark_synced(args):
    """Record the open project as what the PLC runs. Every later login is
    checked against this; get it wrong and a 'read-only' job online-
    changes or downloads the difference (plc_guard.py). Only for when you
    know the PLC runs exactly this project; normally push and install
    record it themselves."""
    if not args.i_know:
        print("mark-synced refused: pass --i-know, and only if the PLC runs",
              file=sys.stderr)
        print("exactly the project that is open now.", file=sys.stderr)
        return E_PROTO
    rep = call_or_exit({"cmd": "exec", "label": "mark-synced",
                        "readonly": True, "snapshot": False,
                        "code": "print(_sync.record('mark-synced'))"},
                       args.timeout)
    print(rep.get("stdout", "").strip())
    return E_OK if rep.get("ok") else E_JOB


def cmd_install(args):
    """Stop the application, download, start: the only way to deploy a
    layout change. It can leave the PLC needing a power cycle when it
    goes wrong, so it insists on someone being at the machine."""
    if not args.on_site:
        print("install refused: pass --on-site, and only when someone can",
              file=sys.stderr)
        print("power-cycle the PLC if the download wedges it.",
              file=sys.stderr)
        print("Check the delta arms are virtual in the project first.",
              file=sys.stderr)
        return E_PROTO
    print("== install (stop_then_install) ==")
    rc, rep = _exec_template("stop_then_install.py", "install",
                             args.timeout, plc="download")
    if rc != E_OK:
        print("install failed; do NOT kill CODESYS while supervisor.py ps",
              file=sys.stderr)
        print("shows a plc:* phase", file=sys.stderr)
        return rc
    print("[layout] baseline: %s" % layout_check.save_baseline(
        layout_check.scan_disk(), "install"))
    return E_OK


def cmd_daemon_start(args):
    """Delegate to the supervisor so there is exactly one place that
    knows how to launch CODESYS. v1 had the launch logic duplicated
    between internals/daemon_kickstart.py and build.sh, which is how
    build.sh ended up pinned to a CODESYS version that was no longer
    installed."""
    import supervisor
    import argparse as _argparse
    return supervisor.cmd_start(_argparse.Namespace(
        wait=args.wait, force=args.force, verbose=args.verbose))


def cmd_doctor(args):
    """One-shot health check. Reports per layer so a debugging session
    starts from 'what is actually broken' rather than guessing."""
    rows = []

    try:
        print(config.describe())
        rows.append(("config", True, config.config_path()))
    except Exception as ex:
        rows.append(("config", False, str(ex)))
        print("config: %s" % ex, file=sys.stderr)

    try:
        proj = config.project_path()
        rows.append(("project file", os.path.isfile(proj), proj))
    except Exception as ex:
        rows.append(("project file", False, str(ex)))

    try:
        exe = config.codesys_exe()
        rows.append(("codesys exe", os.path.isfile(exe), exe))
    except Exception as ex:
        rows.append(("codesys exe", False, str(ex)))

    snap = read_status_file()
    if snap:
        age = time.time() - snap["_mtime"]
        stale = age > config.heartbeat_stale_seconds()
        rows.append(("heartbeat", not stale,
                     "%.0fs old, phase=%s" % (age, snap.get("phase"))))
    else:
        rows.append(("heartbeat", False, "no status file"))

    try:
        rep = call({"cmd": "ping"}, min(args.timeout, 5))
        rows.append(("daemon rpc", bool(rep.get("ok")),
                     "rpc_count=%s dirty=%s online=%s"
                     % (rep.get("rpc_count"), rep.get("dirty"),
                        rep.get("online"))))
    except ConnectionRefusedError:
        rows.append(("daemon rpc", False, "connection refused"))
    except Exception as ex:
        rows.append(("daemon rpc", False, str(ex)))

    try:
        s = socket.create_connection(
            (config.plc_host(), config.plc_port()), timeout=3)
        s.close()
        rows.append(("plc tcp", True,
                     "%s:%d" % (config.plc_host(), config.plc_port())))
    except Exception as ex:
        rows.append(("plc tcp", False, str(ex)))

    print("")
    width = max(len(r[0]) for r in rows)
    bad = 0
    for name, ok, detail in rows:
        if not ok:
            bad += 1
        print("  [%s] %-*s  %s" % ("ok" if ok else "XX", width, name, detail))
    return E_OK if bad == 0 else E_JOB


# ---- argument parsing ------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="rpc.py",
        description="Client for the CODESYS scripting daemon (v2).",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--timeout", type=float, default=600,
                   help="socket timeout in seconds (default 600)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("ping", help="daemon state snapshot")
    sp.set_defaults(func=cmd_ping)
    sp = sub.add_parser("status", help="alias for ping")
    sp.set_defaults(func=cmd_ping)

    sp = sub.add_parser("exec", help="run a job in the warm session")
    sp.add_argument("--file", help="job file (default: read stdin)")
    sp.add_argument("--label", help="short tag for logs")
    sp.add_argument("--readonly", action="store_true",
                    help="job does not mutate: skip snapshot and save")
    sp.add_argument("--no-snapshot", action="store_true",
                    help="mutating job, but skip the pre-job snapshot")
    sp.add_argument("--plc", choices=("keep", "online_change", "download"),
                    default="keep",
                    help="most the job may do to the PLC code. keep "
                         "(default): login only while the project matches "
                         "the PLC; Try becomes Keep, Force/Never refused. "
                         "See plc_guard.py")
    sp.set_defaults(func=cmd_exec)

    sp = sub.add_parser("save", help="save the project without leaving")
    sp.set_defaults(func=cmd_save)

    sp = sub.add_parser("snapshot", help="copy the .project aside now")
    sp.add_argument("--label", default="manual")
    sp.set_defaults(func=cmd_snapshot)

    sp = sub.add_parser("logout", help="drop the online session, keep daemon")
    sp.set_defaults(func=cmd_logout)

    sp = sub.add_parser("read", help="read a symbol through the live session")
    sp.add_argument("symbol")
    sp.set_defaults(func=cmd_read)

    sp = sub.add_parser("write", help="write a symbol")
    sp.add_argument("symbol")
    sp.add_argument("value")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_write)

    sp = sub.add_parser("yield", help="hand the IDE back (daemon exits)")
    sp.add_argument("--reason", default="manual")
    sp.add_argument("--wait", type=int, default=120,
                    help="seconds to watch the release phases")
    sp.add_argument("--hard-stop", action="store_true",
                    help="do not let the supervisor relaunch")
    sp.set_defaults(func=cmd_yield)

    sp = sub.add_parser("stop", help="yield and do not relaunch")
    sp.add_argument("--reason", default="stop")
    sp.add_argument("--wait", type=int, default=120)
    sp.set_defaults(func=cmd_yield, hard_stop=True)

    sp = sub.add_parser("push", help="import .st -> online change -> tests")
    sp.add_argument("--no-tests", action="store_true",
                    help="hot fix: skip the regression suite")
    sp.add_argument("--no-online", action="store_true",
                    help="import and save only, do not touch the PLC "
                         "(also skips the regression)")
    sp.set_defaults(func=cmd_push)

    sp = sub.add_parser("layout", help="check the edit against the layout "
                                       "baseline of the last deploy")
    sp.add_argument("--baseline-from-git", metavar="REV",
                    help="record the baseline from a git revision known "
                         "to match the PLC")
    sp.set_defaults(func=cmd_layout)

    sp = sub.add_parser("install", help="stop app -> download -> start "
                                        "(layout changes; needs --on-site)")
    sp.add_argument("--on-site", action="store_true",
                    help="someone is at the PLC and can power-cycle it")
    sp.set_defaults(func=cmd_install)

    sp = sub.add_parser("mark-synced", help="record the open project as "
                                            "what the PLC runs")
    sp.add_argument("--i-know", action="store_true",
                    help="the PLC runs exactly the open project")
    sp.set_defaults(func=cmd_mark_synced)

    sp = sub.add_parser("daemon-start", help="spawn CODESYS with the daemon")
    sp.add_argument("--wait", type=int, default=180)
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_daemon_start)

    sp = sub.add_parser("doctor", help="per-layer health check")
    sp.set_defaults(func=cmd_doctor)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not hasattr(args, "hard_stop"):
        args.hard_stop = False
    try:
        return args.func(args)
    except config.ConfigError as ex:
        print("config error: %s" % ex, file=sys.stderr)
        return E_PROTO


if __name__ == "__main__":
    sys.exit(main())
