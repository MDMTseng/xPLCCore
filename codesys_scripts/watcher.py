# -*- coding: ascii -*-
# watcher.py -- paste into the CODESYS Scripting Console, OR run via
# Tools -> Scripting -> Execute Script File...
#
# Polls jobs/inbox/ for .py files, executes each inside this warm IDE session,
# captures stdout+stderr to jobs/done/<name>.log, and moves the .py to done/.
# My side (Claude Code) drops job files into inbox/ and reads the .log back.
#
# Keep this running. Stop by closing the IDE, or Ctrl+C in the console.

import os
import sys
import time
import shutil
import traceback

try:
    from cStringIO import StringIO
except ImportError:
    from StringIO import StringIO

ROOT  = r"c:\Users\X1\Desktop\X2.5\TCP_UI\TCP_UI\codesys_scripts\jobs"
INBOX = os.path.join(ROOT, "inbox")
DONE  = os.path.join(ROOT, "done")
STATUS_PATH = os.path.join(ROOT, "watcher.status")
POLL_SEC = 0.4

# Minimum age a .py must have before we execute it. Prevents picking up a file
# that's still being written (partial-write race when the submitter uses plain
# `cp`/`copy` instead of tmp+rename). 250ms is a generous lower bound even for
# slow SMB copies into this Windows path.
MIN_AGE_SEC = 0.25

# Write a heartbeat line to STATUS_PATH at least this often so external probes
# (Claude Code, `Get-ChildItem jobs\watcher.status`, etc.) can detect a stalled
# watcher. File mtime + short contents = liveness signal without stealing focus.
HEARTBEAT_SEC = 2.0

# Project to preload (optional). If already open, we reuse it.
DEFAULT_PROJECT = r"C:\Users\X1\Desktop\XPack2_codesys\PackerX.project"

for d in (INBOX, DONE):
    if not os.path.isdir(d):
        os.makedirs(d)

print("[watcher] INBOX: " + INBOX)
print("[watcher] DONE : " + DONE)
print("[watcher] STATUS: " + STATUS_PATH)


def write_status(state, extra=""):
    """Best-effort heartbeat. Failure must never kill the watcher."""
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        body = "%s  state=%s  pid=%s  %s\n" % (ts, state, os.getpid(), extra)
        f = open(STATUS_PATH, "w")
        try:
            f.write(body)
        finally:
            f.close()
    except Exception:
        pass  # status file errors are non-fatal


def ensure_project():
    """Return True if projects.primary looks usable; try to reopen if not.
    CODESYS won't silently reopen a project that was closed manually, and
    every downstream job does `projects.primary.find(...)` which NoneType's
    otherwise. Check once per cycle so a mid-session close auto-recovers."""
    try:
        if projects.primary is not None:
            return True
    except Exception:
        pass
    try:
        print("[watcher] project not open; reopening " + DEFAULT_PROJECT)
        projects.open(DEFAULT_PROJECT)
        return projects.primary is not None
    except Exception:
        print("[watcher] reopen failed:")
        traceback.print_exc()
        return False


# Preload the project once so the first job is already warm.
try:
    if projects.primary is None:
        print("[watcher] opening project: " + DEFAULT_PROJECT)
        projects.open(DEFAULT_PROJECT)
        print("[watcher] project opened")
    else:
        print("[watcher] reusing already-open project: " + projects.primary.path)
except Exception:
    print("[watcher] preload failed:")
    traceback.print_exc()

print("[watcher] polling every {}s. Drop .py files into inbox/ to run them.".format(POLL_SEC))
write_status("idle", "startup")

def run_job(job_path):
    name = os.path.basename(job_path)
    base = name[:-3] if name.lower().endswith(".py") else name
    log_path = os.path.join(DONE, base + ".log")
    done_py  = os.path.join(DONE, name)

    write_status("running", "job=" + name)

    old_out, old_err = sys.stdout, sys.stderr
    buf = StringIO()
    sys.stdout = buf
    sys.stderr = buf
    t0 = time.time()
    ok = True
    try:
        try:
            f = open(job_path, "r")
            try:
                src = f.read()
            finally:
                f.close()
            code = compile(src, job_path, "exec")
            # Start from our own globals so CODESYS-injected names
            # (projects, system, online, ...) are visible to the job.
            job_globals = dict(globals())
            job_globals["__name__"] = "__main__"
            job_globals["__file__"] = job_path
            exec(code, job_globals)
        except SystemExit:
            pass  # treat sys.exit() as normal termination
        except Exception:
            ok = False
            buf.write("\n[watcher] EXCEPTION while running " + name + ":\n")
            buf.write(traceback.format_exc())
    finally:
        # Restore stdio *before* any other work so a later exception can't
        # leave the scripting console muted.
        sys.stdout, sys.stderr = old_out, old_err

    elapsed = time.time() - t0
    # Write captured output, then move the .py aside.
    try:
        f = open(log_path, "w")
        try:
            f.write(buf.getvalue())
        finally:
            f.close()
    except Exception:
        traceback.print_exc()
    try:
        if os.path.exists(done_py):
            os.remove(done_py)
        shutil.move(job_path, done_py)
    except Exception:
        traceback.print_exc()

    tag = "OK " if ok else "ERR"
    print("[watcher] {} {} ({:.2f}s) -> {}".format(tag, name, elapsed, log_path))
    write_status("idle", "last_job=%s last_ok=%s" % (name, ok))


# Main loop. Using time.sleep blocks the scripting console only; the rest
# of the IDE stays responsive. If the UI feels laggy, reduce POLL_SEC.
last_heartbeat = 0.0
while True:
    # Outer try covers EVERYTHING in the loop body so a transient error can't
    # kill the watcher. Only KeyboardInterrupt is allowed to break out.
    try:
        try:
            entries = sorted(os.listdir(INBOX))
        except Exception:
            entries = []

        # Partial-write guard: only run .py files that have stopped changing
        # for MIN_AGE_SEC. Skipped files get retried on the next poll.
        now = time.time()
        for entry in entries:
            if not entry.lower().endswith(".py"):
                continue
            full = os.path.join(INBOX, entry)
            try:
                age = now - os.path.getmtime(full)
            except Exception:
                age = 0  # if stat fails, skip this cycle
                continue
            if age < MIN_AGE_SEC:
                continue
            # Re-check project is open; if not, try to recover before running.
            if not ensure_project():
                print("[watcher] skip " + entry + " -- no project open")
                continue
            run_job(full)

        # Periodic heartbeat so external probes can verify liveness.
        if (now - last_heartbeat) >= HEARTBEAT_SEC:
            write_status("idle", "queue=%d" % len(entries))
            last_heartbeat = now

    except KeyboardInterrupt:
        print("[watcher] stopped by Ctrl+C.")
        write_status("stopped", "ctrl_c")
        break
    except Exception:
        # Never exit the loop on a non-KeyboardInterrupt error. Log it and
        # continue -- the next poll will try again.
        print("[watcher] outer-loop exception (continuing):")
        traceback.print_exc()
        write_status("error", "see console")

    # sleep() itself can raise KeyboardInterrupt; anything else (very rare)
    # goes through the outer except above on the next iteration.
    try:
        time.sleep(POLL_SEC)
    except KeyboardInterrupt:
        print("[watcher] stopped by Ctrl+C.")
        write_status("stopped", "ctrl_c")
        break
