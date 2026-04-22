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
POLL_SEC = 0.4

# Project to preload (optional). If already open, we reuse it.
DEFAULT_PROJECT = r"C:\Users\X1\Desktop\XPack2_codesys\PackerX.project"

for d in (INBOX, DONE):
    if not os.path.isdir(d):
        os.makedirs(d)

print("[watcher] INBOX: " + INBOX)
print("[watcher] DONE : " + DONE)

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

def run_job(job_path):
    name = os.path.basename(job_path)
    base = name[:-3] if name.lower().endswith(".py") else name
    log_path = os.path.join(DONE, base + ".log")
    done_py  = os.path.join(DONE, name)

    old_out, old_err = sys.stdout, sys.stderr
    buf = StringIO()
    sys.stdout = buf
    sys.stderr = buf
    t0 = time.time()
    ok = True
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

# Main loop. Using time.sleep blocks the scripting console only; the rest
# of the IDE stays responsive. If the UI feels laggy, reduce POLL_SEC.
while True:
    try:
        try:
            entries = sorted(os.listdir(INBOX))
        except Exception:
            entries = []
        for entry in entries:
            if entry.lower().endswith(".py"):
                run_job(os.path.join(INBOX, entry))
    except KeyboardInterrupt:
        print("[watcher] stopped by Ctrl+C.")
        break
    except Exception:
        print("[watcher] outer-loop exception:")
        traceback.print_exc()
    try:
        time.sleep(POLL_SEC)
    except KeyboardInterrupt:
        print("[watcher] stopped by Ctrl+C.")
        break
