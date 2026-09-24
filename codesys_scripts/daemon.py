# -*- coding: ascii -*-
# daemon.py -- RPC server running inside the CODESYS Scripting Console.
#
#   Tools > Scripting > Execute Script File... > pick this file
#   (or let supervisor.py launch CODESYS with --runscript=daemon.py)
#
# ---------------------------------------------------------------------
# v2 design notes -- read this before touching the shutdown path.
# ---------------------------------------------------------------------
#
# v1 had two coupled failure modes:
#
#   (a) While the daemon ran, the IDE was unusable. The only way to get
#       CODESYS back was to stop the daemon, so "stop" always happened at
#       the worst possible moment: after hours of accumulated edits.
#
#   (b) Stopping was just `return True` out of the accept loop. No save,
#       no logout, no close. CODESYS then tore down the IronPython scope
#       while holding a dirty project and often a live online session.
#       If the IDE wanted to raise a modal at that point it could not,
#       because the script still owned the scripting execution context.
#       That is the hang, and the reason it sometimes never came back.
#
# v2 attacks both:
#
#   1. NOTHING ACCUMULATES. Every mutating job ends with proj.save().
#      Saving is no longer opt-in per job template (v1: 14 of 47 job
#      templates called save, and import_all.py explicitly did not).
#      Teardown cost is proportional to accumulated state; drive the
#      accumulation to zero and teardown becomes cheap.
#
#   2. YOU DO NOT HAVE TO LEAVE TO GET WORK DONE. v1 exposed only
#      ping/exec/stop, so "save the project" or "read a PLC variable"
#      meant killing the daemon. v2 serves those over RPC: save, read,
#      write, logout, snapshot, status.
#
#   3. RELEASE IS A FIRST-CLASS OPERATION. `yield` hands the IDE back in
#      a defined order (logout -> save -> drop refs -> close sockets) and
#      leaves CODESYS open with the project loaded, so re-entry is warm.
#
#   4. SHUTDOWN IS OBSERVABLE. Each teardown step publishes its phase to
#      the heartbeat file from a side thread that keeps ticking even when
#      the main thread is blocked inside a CODESYS API call. If release
#      wedges you can now see WHICH STEP wedged instead of guessing.
#
# Wire protocol: one line-delimited JSON object each way.
#   {"cmd":"exec","code":"...","label":"tag","readonly":false,
#    "plc":"keep"}\n      (plc: keep | online_change | download; see
#                          plc_guard.py -- keep is the default)
#   ->{"ok":true,"stdout":"...","elapsed":1.23,"saved":true,...}\n

import os, sys, time, json, socket, select, threading, traceback, shutil

try:
    from cStringIO import StringIO  # IronPython 2.7 fast path
except ImportError:
    try:
        from StringIO import StringIO  # IronPython 2.7 pure-Python fallback
    except ImportError:
        from io import StringIO        # IronPython 3 / CPython 3

# ---- configuration ---------------------------------------------------
# CODESYS does not necessarily put a script's own directory on sys.path.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import config
import plc_guard

HOST = config.rpc_host()
PORT = config.rpc_port()
STATE_DIR = config.state_dir()
SNAPSHOT_DIR = config.snapshot_dir()
STATUS_PATH = os.path.join(STATE_DIR, "daemon.status")
RPC_LOG = os.path.join(STATE_DIR, "daemon.rpc.log")
PROJECT_PATH = config.project_path()
SNAPSHOT_KEEP = config.snapshot_keep()
MAX_JOBS = config.session_max_jobs()
MAX_SECONDS = config.session_max_seconds()

# v1 called os.makedirs() on a hardcoded path belonging to another
# machine, which silently manufactured a bogus tree instead of failing.
# The state dir is ours to create; the project is not ours to invent.
for _d in (STATE_DIR, SNAPSHOT_DIR):
    if not os.path.isdir(_d):
        os.makedirs(_d)

if not os.path.isfile(PROJECT_PATH):
    raise SystemExit(
        "[daemon] configured project does not exist: %s\n"
        "[daemon] fix 'project' in %s" % (PROJECT_PATH, config.config_path()))

# ---- shared state ----------------------------------------------------
state = {
    "phase": "init",
    "since": time.time(),
    "job": "",
    "rpc_count": 0,
    "started": time.time(),
    "last_save": 0.0,
    "dirty": False,
    # Outcome of the save performed during release. The client cannot
    # learn this from the RPC reply -- that is sent before teardown
    # starts -- so it is published through the heartbeat instead.
    "release_save": "-",
}
state_lock = threading.Lock()

# setDaemon(True) only guarantees the thread dies when the PROCESS exits,
# and CODESYS does not exit when a script ends. Without an explicit stop
# every daemon run leaves its heartbeat thread behind, and the next run
# adds another: several threads then overwrite daemon.status once a
# second with their own stale snapshots, so readers see the rpc_count and
# uptime flicker between runs. Stop the thread when the script ends.
_hb_stop = threading.Event()

# A single cached online session reused across read/write RPCs. Logging
# in is slow, and paying it per symbol read would push people back to
# "just stop the daemon and click around", which is what we are fixing.
_online = {"app": None}

# What the PLC runs, as a project fingerprint. Every login is checked
# against it, because any login applies the project/PLC difference.
_sync = plc_guard.ProjectSync(os.path.join(STATE_DIR, "deployed_fp.json"),
                              lambda: projects.primary)


def set_state(phase, job=None):
    state_lock.acquire()
    try:
        state["phase"] = phase
        state["since"] = time.time()
        if job is not None:
            state["job"] = job
    finally:
        state_lock.release()


def snapshot_state():
    state_lock.acquire()
    try:
        return dict(state)
    finally:
        state_lock.release()


def heartbeat_loop():
    """Side thread. Touches no CODESYS API -- only file I/O and the shared
    dict -- so it is safe off the IDE main thread, and critically it keeps
    publishing while the main thread is blocked inside a CODESYS call.
    That is what makes a wedged teardown diagnosable."""
    while not _hb_stop.is_set():
        try:
            snap = snapshot_state()
            elapsed = time.time() - snap["since"]
            line = ("%s phase=%s elapsed=%.1fs rpc_count=%d pid=%s "
                    "uptime=%.0fs dirty=%s save=%s job=%s\n") % (
                time.strftime("%Y-%m-%d %H:%M:%S"),
                snap["phase"], elapsed, snap["rpc_count"], os.getpid(),
                time.time() - snap["started"], snap["dirty"],
                snap["release_save"],
                str(snap["job"]).replace("\n", " ")[:80])
            f = open(STATUS_PATH, "w")
            try:
                f.write(line)
            finally:
                f.close()
        except Exception:
            pass
        _hb_stop.wait(1.0)


def log_rpc(entry):
    try:
        f = open(RPC_LOG, "a")
        try:
            f.write("%s %s\n" % (time.strftime("%H:%M:%S"), entry))
        finally:
            f.close()
    except Exception:
        pass


# ---- project helpers -------------------------------------------------

def ensure_project():
    """Verify projects.primary is usable; reopen if the user closed it
    manually during a yield window."""
    try:
        if projects.primary is not None:
            return True
    except Exception:
        pass
    try:
        projects.open(PROJECT_PATH)
        return projects.primary is not None
    except Exception:
        traceback.print_exc()
        return False


def save_project():
    """Persist the project. Returns (ok, detail)."""
    set_state("saving")
    try:
        proj = projects.primary
        if proj is None:
            return False, "no project open"
        proj.save()
        state_lock.acquire()
        try:
            state["last_save"] = time.time()
            state["dirty"] = False
        finally:
            state_lock.release()
        return True, "saved"
    except Exception as ex:
        return False, "save failed: %s" % (ex,)


def mark_dirty():
    state_lock.acquire()
    try:
        state["dirty"] = True
    finally:
        state_lock.release()


def prune_snapshots():
    try:
        entries = []
        for fn in os.listdir(SNAPSHOT_DIR):
            full = os.path.join(SNAPSHOT_DIR, fn)
            if os.path.isfile(full):
                entries.append((os.path.getmtime(full), full))
        entries.sort()
        while len(entries) > SNAPSHOT_KEEP:
            victim = entries.pop(0)[1]
            try:
                os.remove(victim)
            except Exception:
                pass
    except Exception:
        pass


def snapshot_project(tag):
    """Copy the on-disk .project aside as a rollback point.

    This is only meaningful BECAUSE we save after every job: the file is
    always current when the next job starts, so the snapshot really is
    'the state before this job'. Worst case a crash costs one job."""
    set_state("snapshotting")
    try:
        if not os.path.isfile(PROJECT_PATH):
            return None
        safe = []
        for ch in (tag or "job"):
            safe.append(ch if (ch.isalnum() or ch in "-_") else "_")
        name = "%s_%s%s" % (time.strftime("%Y%m%d-%H%M%S"),
                            "".join(safe)[:40],
                            os.path.splitext(PROJECT_PATH)[1])
        dst = os.path.join(SNAPSHOT_DIR, name)
        shutil.copy2(PROJECT_PATH, dst)
        # copy2 preserves the SOURCE mtime, which here is "when the
        # project was last saved", not "when this rollback point was
        # taken". prune_snapshots() orders by mtime, so leaving it that
        # way means a restored project (whose mtime jumps backwards)
        # would make pruning delete the wrong snapshots. Stamp the copy
        # with its own creation time instead.
        try:
            os.utime(dst, None)
        except OSError:
            pass
        prune_snapshots()
        return dst
    except Exception as ex:
        log_rpc("snapshot-failed: %r" % (ex,))
        return None


# ---- online session --------------------------------------------------

def get_online(login=True):
    """Return a logged-in OnlineApplication, reusing the cached one."""
    app = _online.get("app")
    if app is not None:
        return app
    if not login:
        return None
    proj = projects.primary
    if proj is None:
        raise RuntimeError("no project open")
    found = list(proj.find("Application", True) or [])
    if not found:
        raise RuntimeError("no Application object in project")
    # Keep does NOT merely attach: when the project differs from the PLC
    # it online-changes (code) or downloads (device config) that
    # difference (plc_guard.py). This session only serves symbol
    # read/write, so refuse unless the project matches the PLC.
    ok, why = _sync.check()
    if not ok:
        raise RuntimeError("no PLC session: %s" % why)
    oapp = online.create_online_application(found[0])
    oapp.login(OnlineChangeOption.Keep, False)
    _online["app"] = oapp
    return oapp


def drop_online(quiet=True):
    """Log out the cached session. This is the single most likely thing
    to wedge teardown, so release runs it FIRST and under its own phase."""
    app = _online.get("app")
    _online["app"] = None
    if app is None:
        return True, "no session"
    try:
        app.logout()
        return True, "logged out"
    except Exception as ex:
        if not quiet:
            raise
        return False, "logout failed: %s" % (ex,)


def safe_logout(job_globals):
    """A job that bound its own `oapp` and raised before logging out would
    otherwise leave the next login fighting a stale session."""
    oapp = job_globals.get("oapp")
    if oapp is None or oapp is _online.get("app"):
        return
    try:
        oapp.logout()
    except Exception:
        pass


# ---- job execution ---------------------------------------------------

def exec_job(code, label, readonly=False, want_snapshot=True,
             plc=plc_guard.KEEP):
    """Run a job as a transaction:

        snapshot (unless readonly) -> exec -> safe_logout -> save

    `plc` is the most the job may do to the code on the PLC. The job
    sees a guarded `online` whose login() enforces it (plc_guard.py)."""
    snap_path = None
    if not readonly and want_snapshot:
        snap_path = snapshot_project(label)

    set_state("running", label)
    old_out, old_err = sys.stdout, sys.stderr
    buf = StringIO()
    sys.stdout = buf
    sys.stderr = buf
    t0 = time.time()
    ok = True
    err = ""
    job_globals = dict(globals())
    job_globals["__name__"] = "__main__"
    guard = plc_guard.Guard(
        plc, OnlineChangeOption.Keep, _sync,
        set_phase=lambda phase: set_state(phase),
        log=lambda msg: buf.write(msg + "\n"), label=label)
    job_globals["online"] = plc_guard.GuardedOnline(online, guard)
    try:
        try:
            compiled = compile(code, "<rpc:%s>" % label, "exec")
            exec(compiled, job_globals)
        except SystemExit:
            pass
        except Exception:
            ok = False
            err = traceback.format_exc()
            buf.write("\n[daemon] EXCEPTION:\n" + err)
    finally:
        sys.stdout, sys.stderr = old_out, old_err
        safe_logout(job_globals)

    elapsed = time.time() - t0

    # Jobs write UTF-8 bytes into the capture buffer, because IronPython's
    # cStringIO raises on non-ascii unicode and CODESYS messages here are
    # localised (Chinese on this machine). Decode on the way out so the
    # JSON reply carries real text instead of '?' placeholders -- losing
    # the text would mean losing the content of any build error.
    out = buf.getvalue()
    if not isinstance(out, unicode):
        try:
            out = out.decode("utf-8", "replace")
        except Exception:
            out = out.decode("latin-1", "replace")

    reply = {"ok": ok, "stdout": out, "elapsed": elapsed}
    if snap_path:
        reply["snapshot"] = snap_path
    if not ok:
        reply["error"] = err

    # Save even when the job raised. A job that edited three POUs and
    # then blew up on the fourth has still dirtied the project, and
    # leaving that unsaved is exactly the v1 data-loss path.
    if not readonly:
        mark_dirty()
        saved, detail = save_project()
        reply["saved"] = saved
        if not saved:
            reply["save_detail"] = detail
            reply["ok"] = False

    set_state("idle", "")
    return reply


# ---- symbol access ---------------------------------------------------

def do_read(symbol):
    oapp = get_online()
    return oapp.read_value(symbol)


def do_write(symbol, value, force=False):
    oapp = get_online()
    oapp.set_prepared_value(symbol, str(value))
    if force:
        oapp.force_prepared_values()
    else:
        oapp.write_prepared_values()
    return True


# ---- release / shutdown ----------------------------------------------

_exit = {"requested": False, "reason": "", "restart": False}


def request_exit(reason, restart):
    _exit["requested"] = True
    _exit["reason"] = reason
    _exit["restart"] = restart


def release_ide(reason):
    """Hand the IDE back in a defined order, publishing each phase.

    Order matters. The online session is dropped before the save because
    a live login is the most common thing to block a save, and a blocked
    save under a dirty project is what used to deadlock teardown against
    a modal the IDE could not show."""
    steps = []

    set_state("release:logout", reason)
    ok, detail = drop_online()
    steps.append(("logout", ok, detail))

    set_state("release:save", reason)
    ok, detail = save_project()
    steps.append(("save", ok, detail))
    # Publish the verdict so the client can tell the truth about whether
    # the project actually made it to disk. A save can fail here for
    # reasons that are not exceptions -- a modal the user dismissed comes
    # back as "Operation cancelled by user" -- and reporting "released,
    # project saved" in that case is precisely the lie this daemon exists
    # to prevent.
    state_lock.acquire()
    try:
        state["release_save"] = "ok" if ok else "FAILED"
    finally:
        state_lock.release()

    set_state("release:drop-refs", reason)
    try:
        _online["app"] = None
        steps.append(("drop-refs", True, ""))
    except Exception as ex:
        steps.append(("drop-refs", False, repr(ex)))

    for name, sok, detail in steps:
        log_rpc("release step=%s ok=%s %s" % (name, sok, detail))
    return steps


def session_budget():
    """Recycle before things go bad rather than after. Returns a reason
    string when the session should be retired, else None."""
    snap = snapshot_state()
    if MAX_JOBS and snap["rpc_count"] >= MAX_JOBS:
        return "job budget (%d)" % MAX_JOBS
    if MAX_SECONDS and (time.time() - snap["started"]) >= MAX_SECONDS:
        return "time budget (%ds)" % MAX_SECONDS
    return None


# ---- wire ------------------------------------------------------------

def recv_line(sock, max_bytes=4 * 1024 * 1024):
    parts = []
    total = 0
    while True:
        try:
            chunk = sock.recv(8192)
        except Exception:
            return None
        if not chunk:
            return None
        if isinstance(chunk, bytes):
            try:
                chunk = chunk.decode("utf-8")
            except Exception:
                chunk = chunk.decode("latin-1")
        parts.append(chunk)
        total += len(chunk)
        if total > max_bytes:
            return None
        if "\n" in chunk:
            return "".join(parts).split("\n", 1)[0]


def send_json(sock, obj):
    payload = json.dumps(obj) + "\n"
    try:
        sock.sendall(payload.encode("utf-8"))
    except Exception:
        try:
            sock.sendall(payload)
        except Exception:
            pass


def handle_client(sock):
    """Returns True when the accept loop should exit."""
    try:
        sock.settimeout(300)
        line = recv_line(sock)
        if line is None:
            return False
        try:
            req = json.loads(line)
        except Exception as ex:
            send_json(sock, {"ok": False, "error": "bad json: %s" % ex})
            return False

        cmd = req.get("cmd")

        if cmd in ("ping", "status"):
            snap = snapshot_state()
            send_json(sock, {
                "ok": True,
                "phase": snap["phase"],
                "since_elapsed": time.time() - snap["since"],
                "job": snap["job"],
                "rpc_count": snap["rpc_count"],
                "uptime": time.time() - snap["started"],
                "dirty": snap["dirty"],
                "last_save_age": (time.time() - snap["last_save"]
                                  if snap["last_save"] else None),
                "online": _online.get("app") is not None,
                "budget": session_budget(),
                "project": PROJECT_PATH,
            })
            return False

        if cmd == "save":
            ok, detail = save_project()
            send_json(sock, {"ok": ok, "detail": detail})
            set_state("idle", "")
            return False

        if cmd == "snapshot":
            path = snapshot_project(req.get("label") or "manual")
            send_json(sock, {"ok": path is not None, "snapshot": path})
            set_state("idle", "")
            return False

        if cmd == "logout":
            ok, detail = drop_online()
            send_json(sock, {"ok": ok, "detail": detail})
            set_state("idle", "")
            return False

        if cmd == "read":
            symbol = req.get("symbol")
            if not symbol:
                send_json(sock, {"ok": False, "error": "missing 'symbol'"})
                return False
            set_state("reading", symbol)
            try:
                value = do_read(symbol)
                send_json(sock, {"ok": True, "symbol": symbol,
                                 "value": str(value)})
            except Exception as ex:
                send_json(sock, {"ok": False, "symbol": symbol,
                                 "error": "%s" % ex,
                                 "traceback": traceback.format_exc()})
            set_state("idle", "")
            return False

        if cmd == "write":
            symbol = req.get("symbol")
            if not symbol or "value" not in req:
                send_json(sock, {"ok": False,
                                 "error": "need 'symbol' and 'value'"})
                return False
            set_state("writing", symbol)
            try:
                do_write(symbol, req.get("value"), bool(req.get("force")))
                mark_dirty()
                send_json(sock, {"ok": True, "symbol": symbol,
                                 "value": req.get("value"),
                                 "forced": bool(req.get("force"))})
            except Exception as ex:
                send_json(sock, {"ok": False, "symbol": symbol,
                                 "error": "%s" % ex,
                                 "traceback": traceback.format_exc()})
            set_state("idle", "")
            return False

        if cmd in ("yield", "release", "stop"):
            # `yield`/`release` mean "give me the IDE back, I am coming
            # back later" -- the supervisor may relaunch. `stop` means
            # "we are done" -- it must not be restarted.
            restart = cmd in ("yield", "release")
            reason = req.get("reason") or cmd
            send_json(sock, {"ok": True, "releasing": True,
                             "restart": restart, "reason": reason})
            request_exit(reason, restart)
            return True

        if cmd == "exec":
            code = req.get("code", "")
            label = (req.get("label") or "(unnamed)")[:60]
            readonly = bool(req.get("readonly"))
            want_snapshot = req.get("snapshot", True)
            plc = req.get("plc") or plc_guard.KEEP
            if plc not in plc_guard.MODES:
                send_json(sock, {"ok": False, "stdout": "", "elapsed": 0.0,
                                 "error": "unknown plc mode %r" % (plc,)})
                return False
            if not ensure_project():
                send_json(sock, {"ok": False, "error": "no project open",
                                 "stdout": "", "elapsed": 0.0})
                log_rpc("NOPROJ label=%s" % label)
                return False
            reply = exec_job(code, label, readonly, want_snapshot, plc)
            state_lock.acquire()
            try:
                state["rpc_count"] += 1
                reply["rpc_count"] = state["rpc_count"]
            finally:
                state_lock.release()
            budget = session_budget()
            if budget:
                reply["budget_exceeded"] = budget
            send_json(sock, reply)
            log_rpc("ok=%s elapsed=%.2fs saved=%s plc=%s label=%s" % (
                reply.get("ok"), reply.get("elapsed", 0.0),
                reply.get("saved"), plc, label))
            if budget:
                # Retire cleanly rather than letting the session rot.
                log_rpc("budget-exit %s" % budget)
                request_exit("budget: %s" % budget, True)
                return True
            return False

        send_json(sock, {"ok": False, "error": "unknown cmd: %r" % cmd})
        return False
    finally:
        try:
            sock.close()
        except Exception:
            pass


# ---- main ------------------------------------------------------------

print("[daemon] v2 starting on %s:%d" % (HOST, PORT))
print("[daemon] project: %s" % PROJECT_PATH)
print("[daemon] state:   %s" % STATE_DIR)

hb = threading.Thread(target=heartbeat_loop)
hb.setDaemon(True)
hb.start()

if ensure_project():
    try:
        print("[daemon] project ready: %s" % projects.primary.path)
    except Exception:
        print("[daemon] project ready (path read failed)")
else:
    print("[daemon] WARN: no project open and reopen failed")

# Headless prompts. With the default (PromptHandling.None) every CODESYS
# message box -- "Error while downloading ... continue with the remaining
# items?" -- waits on screen for a person, and the daemon's job blocks with
# it. CODESYS runs elevated, so nothing outside can click it either; on
# 2026-09-24 that meant killing CODESYS mid-download, which left the PLC's
# application stuck in "exit" until the controller was power-cycled.
# ProcessScriptPrompts answers from system.prompt_answers (else the
# prompt's default); the Log* flags record prompts and their message keys
# in the message view, so an answer can be pinned later if a default is
# wrong.
try:
    system.prompt_handling = (PromptHandling.ProcessScriptPrompts
                              | PromptHandling.LogSimplePrompts
                              | PromptHandling.LogMessageKeys)
    print("[daemon] prompt handling: %s" % system.prompt_handling)
except Exception as ex:
    print("[daemon] WARN: could not set prompt handling: %s" % ex)

srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    srv.bind((HOST, PORT))
except Exception as ex:
    print("[daemon] bind failed: %s" % ex)
    print("[daemon] another daemon already running? Try `rpc.py yield`.")
    raise
srv.listen(4)
# Blocking listen socket, woken by select() with a 0.5 s timeout (keeps
# Ctrl+C and exit requests responsive). A socket *timeout* on the listening
# socket is what made IronPython's accept() fail with WSAEINVAL after dense
# back-to-back connections.
srv.settimeout(None)
set_state("idle", "")
log_rpc("daemon-start pid=%s project=%s" % (os.getpid(), PROJECT_PATH))
print("[daemon] listening. Ctrl+C here, or `rpc.py yield`, to hand back the IDE.")

# Accept-error throttle. A broken listening socket (WSAEINVAL 10022 on
# Windows) used to make accept() raise every iteration, spinning at 100%
# CPU and flooding the log until CODESYS fell over. WSAEINVAL reliably
# appears here after dense back-to-back connections (a pytest run firing
# many rpc calls); closing and rebinding recovers without a restart.
ACCEPT_ERR_SLEEP = 1.0
ACCEPT_ERR_MAX = 5
accept_err_streak = 0
WSAEINVAL = 10022


def rebind_listen_socket():
    global srv
    try:
        srv.close()
    except Exception:
        pass
    new_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    new_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    new_srv.bind((HOST, PORT))
    new_srv.listen(4)
    new_srv.settimeout(None)
    srv = new_srv
    return new_srv


def listen_socket_works():
    """Connect to ourselves and check the connection reaches *this*
    listening socket. After a WSAEINVAL rebind on 2026-09-24 the port kept
    accepting connections that nothing ever served -- the daemon looked
    alive and answered no one for 11 minutes."""
    probe = None
    try:
        probe = socket.create_connection((HOST, PORT), 2)
        r, _, _ = select.select([srv], [], [], 2.0)
        if not r:
            return False
        c, _ = srv.accept()
        c.close()
        return True
    except Exception:
        return False
    finally:
        if probe is not None:
            try:
                probe.close()
            except Exception:
                pass


try:
    while not _exit["requested"]:
        try:
            readable, _, _ = select.select([srv], [], [], 0.5)
            if not readable:
                continue
            client, _addr = srv.accept()
            client.settimeout(None)
            accept_err_streak = 0
        except socket.timeout:
            continue
        except KeyboardInterrupt:
            print("[daemon] Ctrl+C -- releasing IDE")
            request_exit("KeyboardInterrupt", False)
            break
        except Exception as ex:
            accept_err_streak += 1
            args = getattr(ex, "args", ())
            is_wsaeinval = (len(args) >= 1 and isinstance(args[0], int)
                            and args[0] == WSAEINVAL)
            if accept_err_streak == 1 or accept_err_streak >= ACCEPT_ERR_MAX:
                log_rpc("accept-exception (streak=%d wsaeinval=%s): %s" % (
                    accept_err_streak, is_wsaeinval, repr(ex)[:200]))
            if is_wsaeinval:
                try:
                    srv = rebind_listen_socket()
                    if not listen_socket_works():
                        log_rpc("loop-exit reason=rebind-not-serving")
                        print("[daemon] listening socket rebound but not "
                              "serving -- exiting instead of hanging")
                        request_exit("rebind-not-serving", True)
                        break
                    log_rpc("accept-rebound after WSAEINVAL streak=%d (verified)"
                            % accept_err_streak)
                    accept_err_streak = 0
                    continue
                except Exception as rebind_ex:
                    log_rpc("rebind-failed: %s" % repr(rebind_ex)[:200])
            if accept_err_streak >= ACCEPT_ERR_MAX:
                log_rpc("loop-exit reason=accept-error-streak")
                print("[daemon] giving up after %d accept errors"
                      % ACCEPT_ERR_MAX)
                request_exit("accept-error-streak", True)
                break
            time.sleep(ACCEPT_ERR_SLEEP)
            continue

        try:
            if handle_client(client):
                break
        except KeyboardInterrupt:
            log_rpc("handler-exit reason=KeyboardInterrupt")
            request_exit("KeyboardInterrupt", False)
            break
        except Exception as ex:
            log_rpc("handler-exception: %s" % repr(ex)[:200])
            traceback.print_exc()

except KeyboardInterrupt:
    log_rpc("daemon-keyboardinterrupt")
    request_exit("KeyboardInterrupt", False)
except BaseException as ex:
    # Log the FULL traceback, not repr(ex): if something escapes this
    # far the frames are the only useful part, and a truncated repr sends
    # them nowhere. Credit to 83a5fb1 on main, which made the same call.
    log_rpc("daemon-fatal: %s" % repr(ex)[:200])
    log_rpc(traceback.format_exc())
    traceback.print_exc()
    request_exit("fatal", True)
finally:
    # Close the listener FIRST so no new client can arrive mid-teardown
    # and re-dirty the project we are about to save.
    set_state("release:socket", _exit.get("reason", ""))
    try:
        srv.close()
    except Exception:
        pass

    release_ide(_exit.get("reason") or "shutdown")

    set_state("stopped", _exit.get("reason", ""))
    log_rpc("daemon-stop pid=%s reason=%s restart=%s" % (
        os.getpid(), _exit.get("reason"), _exit.get("restart")))

    # Let the heartbeat publish the final 'stopped' line, then stop it.
    # CODESYS keeps running after the script returns, so a thread left
    # alive here would fight the next run's heartbeat over the file.
    time.sleep(1.2)
    _hb_stop.set()
    # Signalling alone is not enough. The script can return while the
    # thread is still inside its final iteration, and IronPython then has
    # to abort a live thread during interpreter teardown -- which surfaces
    # as a "Running script daemon.py caused exception / ArgumentNullException
    # (param del)" modal. A modal nobody can answer is exactly how a
    # release turns into a hang, so wait for the thread to actually end.
    try:
        hb.join(3.0)
    except Exception:
        pass

    print("[daemon] released. IDE is yours. reason=%s"
          % (_exit.get("reason") or "shutdown"))
    if _exit.get("restart"):
        print("[daemon] (supervisor may relaunch; project is saved)")
