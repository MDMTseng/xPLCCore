#!/usr/bin/env python3
"""supervisor.py -- owns the CODESYS process lifecycle from outside.

Why this exists
---------------
The daemon runs INSIDE CODESYS, so it cannot rescue itself: if the
scripting context wedges, the thing that would notice is the thing that
is stuck. The supervisor is a plain CPython process that watches from
the outside, using the heartbeat file (written by the daemon's side
thread, which keeps ticking even when its main thread is blocked).

Killing CODESYS is only an acceptable recovery because the daemon saves
after every job. That invariant is what turns "the IDE is hung and I may
lose hours of work" into "the IDE is hung, kill it, reopen, lose at most
the last job". Do not weaken the save-per-job rule without revisiting
this file.

Commands
--------
    supervisor.py start            launch CODESYS running the daemon
    supervisor.py watch            monitor; report (and optionally recover)
    supervisor.py kill             force-kill CODESYS after a safety check
    supervisor.py snapshots        list rollback points
    supervisor.py restore <file>   put a snapshot back as the live project
    supervisor.py ps               show CODESYS processes
"""

import sys
import os
import time
import json
import shutil
import argparse
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import config

DAEMON = os.path.join(HERE, "daemon.py")


def status_path():
    return os.path.join(config.state_dir(), "daemon.status")


def read_status():
    path = status_path()
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            line = f.read().strip()
    except OSError:
        return None
    if not line:
        return None
    out = {"_raw": line, "_age": time.time() - os.path.getmtime(path)}
    for token in line.split():
        if "=" in token:
            k, v = token.split("=", 1)
            out[k] = v
    return out


def codesys_cmdline(script, scriptargs=None, noui=False):
    """Build the CODESYS command line as a STRING, not an argv list.

    CODESYS parses the raw command line itself, splitting on spaces and
    honouring only quotes that are literally part of an argument. So it
    needs:

        --profile="CODESYS V3.5 SP22 Patch 3"

    Passing an argv list to subprocess produces Python's own quoting --
    `"--profile=CODESYS V3.5 SP22 Patch 3"`, with the quotes around the
    whole token -- and CODESYS then reads the profile as just "CODESYS"
    and dies with 'version profile is invalid'. Verified on 3.5.22.30:
    list form fails, embedded-quote string form works.
    """
    parts = ['"%s"' % config.codesys_exe(),
             '--profile="%s"' % config.codesys_profile()]
    if noui:
        parts.append("--noUI")
    parts.append('--runscript="%s"' % script)
    if scriptargs:
        parts.append('--scriptargs="%s"' % " ".join(scriptargs))
    return " ".join(parts)


def codesys_processes():
    """Return [(pid, name)] for running CODESYS IDE processes.

    The IDE registers itself as 'CODESYS 64 <version>' rather than
    'CODESYS.exe', so match on the prefix instead of an exact name.
    """
    out = []
    try:
        res = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=30)
    except Exception:
        return out
    for line in res.stdout.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) < 2:
            continue
        name = parts[0]
        if name.startswith("CODESYS") and "Control" not in name \
                and "Gateway" not in name and "SysTray" not in name:
            try:
                out.append((int(parts[1]), name))
            except ValueError:
                pass
    return out


# ---- commands --------------------------------------------------------

def cmd_start(args):
    exe = config.codesys_exe()
    if not os.path.isfile(exe):
        print("CODESYS not found: %s" % exe, file=sys.stderr)
        return 2

    running = codesys_processes()
    if running and not args.force:
        print("CODESYS already running: %s"
              % ", ".join("%s(%d)" % (n, p) for p, n in running))
        print("The daemon must be started from inside that instance:")
        print("  Tools > Scripting > Execute Script File... > %s" % DAEMON)
        print("Or re-run with --force to launch a second instance.")
        return 1

    # Deliberately NO --noUI: the whole point is that you can take the
    # IDE back with `rpc.py yield` and keep working in the same window.
    cmd = codesys_cmdline(DAEMON, noui=False)
    print("launching: %s" % cmd)
    subprocess.Popen(cmd, close_fds=True)

    deadline = time.time() + args.wait
    print("waiting for the daemon to report idle...")
    while time.time() < deadline:
        snap = read_status()
        if snap and snap.get("phase") == "idle" and snap["_age"] < 10:
            print("daemon up: %s" % snap["_raw"])
            return 0
        time.sleep(1.0)
    print("daemon did not come up within %ds" % args.wait, file=sys.stderr)
    snap = read_status()
    if snap:
        print("last heartbeat: %s" % snap["_raw"], file=sys.stderr)
    return 3


def cmd_watch(args):
    stale_after = args.stale or config.heartbeat_stale_seconds()
    print("watching %s (stale after %ds, recover=%s)"
          % (status_path(), stale_after, args.recover))
    last_raw = None
    wedged_since = None
    while True:
        snap = read_status()
        if snap is None:
            print("[%s] no heartbeat file" % time.strftime("%H:%M:%S"))
            time.sleep(args.interval)
            continue

        if snap["_raw"] != last_raw:
            last_raw = snap["_raw"]
            if args.verbose:
                print("[%s] %s" % (time.strftime("%H:%M:%S"), snap["_raw"]))

        phase = snap.get("phase", "?")
        try:
            in_phase = float(snap.get("elapsed", "0s").rstrip("s"))
        except ValueError:
            in_phase = 0.0

        hung = snap["_age"] > stale_after
        stuck_release = phase.startswith("release:") and in_phase > stale_after

        if hung or stuck_release:
            if wedged_since is None:
                wedged_since = time.time()
                why = ("heartbeat %.0fs stale" % snap["_age"] if hung
                       else "stuck in %s for %.0fs" % (phase, in_phase))
                print("[%s] WEDGED: %s" % (time.strftime("%H:%M:%S"), why))
                print("        %s" % snap["_raw"])
                if not args.recover:
                    print("        (pass --recover to kill and relaunch)")
            elif args.recover and (time.time() - wedged_since) > args.grace:
                print("[%s] recovering" % time.strftime("%H:%M:%S"))
                rc = cmd_kill(argparse.Namespace(yes=True, verbose=args.verbose))
                if rc == 0:
                    time.sleep(3)
                    cmd_start(argparse.Namespace(wait=180, force=False))
                wedged_since = None
        else:
            wedged_since = None

        time.sleep(args.interval)


def cmd_kill(args):
    procs = codesys_processes()
    if not procs:
        print("no CODESYS IDE process running")
        return 0

    snap = read_status()
    dirty = snap.get("dirty") if snap else None
    phase = snap.get("phase") if snap else None

    print("about to force-kill: %s"
          % ", ".join("%s(%d)" % (n, p) for p, n in procs))
    print("  last heartbeat: %s" % (snap["_raw"] if snap else "<none>"))

    # dirty=True means a job mutated the project and the save had not
    # completed. Saving happens at the end of every job, so this window
    # is one job wide -- but say so plainly rather than pretending the
    # kill is free.
    if dirty == "True":
        print("")
        print("  WARNING: daemon reports dirty=True (phase=%s)." % phase)
        print("  Unsaved work is at most the job in flight. The last")
        print("  snapshot is your rollback point:")
        snaps = list_snapshots()
        if snaps:
            print("    %s" % snaps[-1][1])

    if not args.yes:
        print("")
        print("re-run with --yes to actually kill")
        return 1

    rc = 0
    for pid, name in procs:
        res = subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                             capture_output=True, text=True)
        print("  taskkill %d (%s): rc=%d" % (pid, name, res.returncode))
        if res.returncode != 0:
            rc = res.returncode
            if args.verbose:
                print(res.stdout or res.stderr)
    return rc


def list_snapshots():
    d = config.snapshot_dir()
    if not os.path.isdir(d):
        return []
    rows = []
    for fn in sorted(os.listdir(d)):
        full = os.path.join(d, fn)
        if os.path.isfile(full):
            rows.append((os.path.getmtime(full), full))
    rows.sort()
    return rows


def cmd_snapshots(args):
    rows = list_snapshots()
    if not rows:
        print("no snapshots in %s" % config.snapshot_dir())
        return 0
    now = time.time()
    for mtime, path in rows:
        print("  %s  %7.1f MB  %5.0f min ago  %s" % (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime)),
            os.path.getsize(path) / 1e6, (now - mtime) / 60,
            os.path.basename(path)))
    print("")
    print("restore with: supervisor.py restore <filename>")
    return 0


def cmd_restore(args):
    src = args.snapshot
    if not os.path.isabs(src):
        src = os.path.join(config.snapshot_dir(), src)
    if not os.path.isfile(src):
        print("no such snapshot: %s" % src, file=sys.stderr)
        return 2

    target = config.project_path()
    if codesys_processes() and not args.yes:
        print("CODESYS is running. Close it (or supervisor.py kill --yes)",
              file=sys.stderr)
        print("before overwriting the live project.", file=sys.stderr)
        return 1

    if os.path.isfile(target):
        aside = target + ".before-restore-%s" % time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(target, aside)
        print("current project set aside: %s" % aside)

    shutil.copy2(src, target)
    print("restored %s -> %s" % (os.path.basename(src), target))
    return 0


def cmd_build(args):
    """Cold headless build. Spawns a fresh CODESYS, compiles, exits.

    Immune to every failure mode this file exists to handle: the process
    terminates when the script finishes, so there is no session to wedge
    and nothing left unsaved. Use it for CI and for sanity-checking when
    the daemon is down or you do not want to disturb a warm session."""
    exe = config.codesys_exe()
    script = os.path.join(HERE, "internals", "build.py")
    if not os.path.isfile(script):
        print("missing build script: %s" % script, file=sys.stderr)
        return 2

    log = os.path.join(config.state_dir(), "build.log")
    try:
        os.remove(log)
    except OSError:
        pass

    # With --noUI CODESYS exits on its own once the script returns.
    # Without it the window stays up so you can watch, but the process
    # does not self-terminate -- which is why v1's build.sh had to
    # background the call and poll for the log file.
    # CODESYS splits --scriptargs on whitespace with no way to quote an
    # individual value (verified on 3.5.22.30: a two-path scriptargs
    # arrives as sys.argv[1:] == [path1, path2]). A path containing a
    # space would therefore arrive shredded, so in that case pass
    # nothing and let build.py fall back to reading config itself.
    scriptargs = [config.project_path(), log]
    if any(" " in a for a in scriptargs):
        print("  (path contains a space; build.py will read config instead)")
        scriptargs = None

    cmd = codesys_cmdline(script, scriptargs=scriptargs,
                          noui=not args.show_ui)

    print("building: %s" % config.project_path())
    if args.verbose:
        print("  %s" % cmd)
    t0 = time.time()
    try:
        res = subprocess.run(cmd, timeout=args.timeout)
        rc = res.returncode
    except subprocess.TimeoutExpired:
        print("build timed out after %ds" % args.timeout, file=sys.stderr)
        rc = 124

    print("---- build.log ----")
    if os.path.isfile(log):
        with open(log, "r", encoding="utf-8", errors="replace") as f:
            sys.stdout.write(f.read())
    else:
        print("(no log written)")
    print("---- codesys exit=%d in %.0fs ----" % (rc, time.time() - t0))
    return rc


def cmd_ps(args):
    procs = codesys_processes()
    if not procs:
        print("no CODESYS IDE process running")
    for pid, name in procs:
        print("  %6d  %s" % (pid, name))
    snap = read_status()
    print("")
    print("heartbeat: %s" % (snap["_raw"] if snap else "<none>"))
    if snap:
        print("age: %.0fs" % snap["_age"])
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="supervisor.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("start", help="launch CODESYS running the daemon")
    sp.add_argument("--wait", type=int, default=180)
    sp.add_argument("--force", action="store_true",
                    help="launch even if CODESYS is already running")
    sp.set_defaults(func=cmd_start)

    sp = sub.add_parser("watch", help="monitor the heartbeat")
    sp.add_argument("--interval", type=float, default=5.0)
    sp.add_argument("--stale", type=int, default=0,
                    help="seconds of silence before calling it wedged")
    sp.add_argument("--recover", action="store_true",
                    help="kill and relaunch when wedged")
    sp.add_argument("--grace", type=int, default=60,
                    help="wait this long after detecting a wedge")
    sp.set_defaults(func=cmd_watch)

    sp = sub.add_parser("kill", help="force-kill CODESYS")
    sp.add_argument("--yes", action="store_true")
    sp.set_defaults(func=cmd_kill)

    sp = sub.add_parser("snapshots", help="list rollback points")
    sp.set_defaults(func=cmd_snapshots)

    sp = sub.add_parser("restore", help="restore a snapshot")
    sp.add_argument("snapshot")
    sp.add_argument("--yes", action="store_true")
    sp.set_defaults(func=cmd_restore)

    sp = sub.add_parser("build", help="cold headless build (no daemon)")
    sp.add_argument("--show-ui", action="store_true",
                    help="keep the IDE window visible (will not self-exit)")
    sp.add_argument("--timeout", type=int, default=900)
    sp.set_defaults(func=cmd_build)

    sp = sub.add_parser("ps", help="show CODESYS processes and heartbeat")
    sp.set_defaults(func=cmd_ps)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("")
        return 130
    except config.ConfigError as ex:
        print("config error: %s" % ex, file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
