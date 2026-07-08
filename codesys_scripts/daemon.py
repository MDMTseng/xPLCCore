# -*- coding: ascii -*-
# daemon.py -- run inside the CODESYS Scripting Console (Tools -> Scripting
# -> Execute Script File... or paste).
#
# Replaces the file-polling watcher.py with a TCP RPC server. Each submission
# is one line-delimited-JSON round-trip on 127.0.0.1:7420.
#
#   request : {"cmd":"exec","code":"<source>","label":"short tag"}\n
#   reply   : {"ok":true,"stdout":"...","elapsed":1.23}\n
#             {"ok":false,"error":"<traceback>","stdout":"...","elapsed":...}
#   ping    : {"cmd":"ping"} -> {"ok":true,"phase":"idle",...}
#   stop    : {"cmd":"stop"} -> {"ok":true} (server exits its accept loop)
#
# Telemetry:
#   jobs/daemon.status   -- 1Hz heartbeat; written from a side thread so it
#                           keeps ticking even when a job is running. Lets us
#                           see "currently running job=X for 47s" instead of
#                           wondering if the server died.
#   jobs/daemon.rpc.log  -- one line per RPC: timestamp, ok, elapsed, label.
#                           Permanent forensic trail; survives restarts.
#
# Failure-mode hardening (relative to watcher.py):
#   * heartbeat thread is decoupled from job execution -- if a job wedges
#     inside a CODESYS API call, status keeps updating with phase=running
#     and the elapsed-since timer climbs, so I can spot stuck jobs.
#   * every exec block has a finally that calls safe_logout() on any `oapp`
#     left in the job's globals, so a job that raises mid-login can't leave
#     the next job stuck on a login-conflict.
#   * connection-refused gives the client an immediate signal instead of
#     the silent "file sat in inbox forever" failure mode.
#
# Stop with Ctrl+C in the console, or send {"cmd":"stop"}.

import os, sys, time, json, socket, threading, traceback
try:
    from cStringIO import StringIO  # IronPython 2.7 fast path
except ImportError:
    try:
        from StringIO import StringIO  # IronPython 2.7 pure-Python fallback
    except ImportError:
        from io import StringIO        # IronPython 3 / CPython 3

HOST = "127.0.0.1"
PORT = 7420

ROOT = r"c:\Users\X1\Desktop\X2.5\TCP_UI\TCP_UI\codesys_scripts\jobs"
STATUS_PATH = os.path.join(ROOT, "daemon.status")
RPC_LOG     = os.path.join(ROOT, "daemon.rpc.log")
DEFAULT_PROJECT = r"C:\Users\X1\Desktop\XPack2_codesys\PackerX.project"

if not os.path.isdir(ROOT):
    os.makedirs(ROOT)

# Shared state between the accept loop and the heartbeat thread.
state = {"phase": "init", "since": time.time(), "job": "", "rpc_count": 0}
state_lock = threading.Lock()

# Stop signal for the heartbeat thread. Without it the thread ran `while
# True` forever, so on daemon shutdown the main script returned while the
# thread was still live and IronPython had to abort a running thread
# during interpreter teardown -- a classic source of the
# "Running script daemon.py caused exception / ArgumentNullException
# (param del)" modal dialog. We now signal + join it before returning.
hb_stop = threading.Event()


def set_state(phase, job=""):
    state_lock.acquire()
    try:
        state["phase"] = phase
        state["since"] = time.time()
        state["job"]   = job
    finally:
        state_lock.release()


def heartbeat_loop():
    """Runs in a daemon thread. Touches no CODESYS API -- only file I/O and
    the shared state dict -- so it's safe to run off the IDE main thread.
    Exits when hb_stop is set so shutdown can join it cleanly (no lingering
    thread for IronPython to abort during teardown)."""
    # The ENTIRE body (including the wait) is inside try/except so an
    # unhandled exception can never kill the thread and surface to the
    # scripting host as a script-level failure.
    while not hb_stop.is_set():
        try:
            state_lock.acquire()
            try:
                snap = dict(state)
            finally:
                state_lock.release()
            elapsed = time.time() - snap["since"]
            line = "%s phase=%s elapsed=%.1fs rpc_count=%d pid=%s job=%s\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"),
                snap["phase"], elapsed, snap["rpc_count"], os.getpid(),
                snap["job"].replace("\n", " ")[:80],
            )
            f = open(STATUS_PATH, "w")
            try: f.write(line)
            finally: f.close()
            # Interruptible sleep: wakes immediately when stop is signalled.
            hb_stop.wait(1.0)
        except Exception:
            hb_stop.wait(1.0)


def append_rpc_log(entry):
    try:
        f = open(RPC_LOG, "a")
        try: f.write(entry + "\n")
        finally: f.close()
    except Exception:
        pass


def ensure_project():
    """Best-effort verify projects.primary is usable. Re-opens DEFAULT_PROJECT
    if the user closed it manually mid-session."""
    try:
        if projects.primary is not None:
            return True
    except Exception:
        pass
    try:
        projects.open(DEFAULT_PROJECT)
        return projects.primary is not None
    except Exception:
        traceback.print_exc()
        return False


def safe_logout(job_globals):
    """If the job left an online application bound as `oapp`, force a logout.
    A job that raised between `oapp.login(...)` and its own `oapp.logout()`
    would otherwise leave the next job's login fighting a stale session --
    one of the prime suspects for the watcher's hangs (S2 in the debug
    table). Best-effort: any exception from logout is swallowed."""
    oapp = job_globals.get("oapp")
    if oapp is None:
        return
    try:
        oapp.logout()
    except Exception:
        pass


def exec_job(code, label):
    """Compile + exec source in a fresh dict seeded from the daemon's globals
    so the CODESYS-injected names (projects, online, system, OnlineChangeOption)
    remain visible. Captures stdout+stderr. Returns (ok, output, elapsed, err)."""
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
    try:
        try:
            compiled = compile(code, "<rpc:" + label + ">", "exec")
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
    set_state("idle", "")
    return ok, buf.getvalue(), elapsed, err


def recv_line(sock, max_bytes=4 * 1024 * 1024):
    """Read until '\\n' or EOF. Returns the line as a str (no trailing \\n),
    or None on EOF / error / oversize."""
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
            try: chunk = chunk.decode("utf-8")
            except Exception: chunk = chunk.decode("latin-1")
        parts.append(chunk)
        total += len(chunk)
        if total > max_bytes:
            return None
        if "\n" in chunk:
            joined = "".join(parts)
            return joined.split("\n", 1)[0]


def send_json(sock, obj):
    payload = (json.dumps(obj) + "\n")
    try:
        sock.sendall(payload.encode("utf-8"))
    except Exception:
        try: sock.sendall(payload)
        except Exception: pass


def handle_client(sock):
    try:
        sock.settimeout(120)
        line = recv_line(sock)
        if line is None:
            return False
        try:
            req = json.loads(line)
        except Exception as ex:
            send_json(sock, {"ok": False, "error": "bad json: " + str(ex)})
            return False
        cmd = req.get("cmd")
        if cmd == "ping":
            state_lock.acquire()
            try:
                snap = dict(state)
            finally:
                state_lock.release()
            send_json(sock, {
                "ok": True,
                "phase": snap["phase"],
                "since_elapsed": time.time() - snap["since"],
                "job": snap["job"],
                "rpc_count": snap["rpc_count"],
            })
            return False
        if cmd == "stop":
            send_json(sock, {"ok": True, "stopping": True})
            return True
        if cmd == "exec":
            code  = req.get("code", "")
            label = (req.get("label") or "")[:60]
            if not ensure_project():
                send_json(sock, {"ok": False, "error": "no project open",
                                 "stdout": "", "elapsed": 0.0})
                append_rpc_log("%s NOPROJ label=%s" % (
                    time.strftime("%H:%M:%S"), label))
                return False
            ok, output, elapsed, err = exec_job(code, label or "(unnamed)")
            state_lock.acquire()
            try:
                state["rpc_count"] += 1
                rpc_count = state["rpc_count"]
            finally:
                state_lock.release()
            reply = {"ok": ok, "stdout": output, "elapsed": elapsed,
                     "rpc_count": rpc_count}
            if not ok:
                reply["error"] = err
            send_json(sock, reply)
            append_rpc_log("%s ok=%s elapsed=%.2fs label=%s" % (
                time.strftime("%H:%M:%S"), ok, elapsed, label))
            return False
        send_json(sock, {"ok": False, "error": "unknown cmd: " + repr(cmd)})
        return False
    finally:
        try: sock.close()
        except Exception: pass


# ---- main ----
print("[daemon] starting on %s:%d" % (HOST, PORT))

hb = threading.Thread(target=heartbeat_loop)
hb.setDaemon(True)
hb.start()

# Preload project so the first RPC is warm.
if ensure_project():
    try:
        print("[daemon] project ready: " + projects.primary.path)
    except Exception:
        print("[daemon] project ready (path read failed)")
else:
    print("[daemon] WARN: no project open and reopen failed")

srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    srv.bind((HOST, PORT))
except Exception as ex:
    print("[daemon] bind failed: %s" % ex)
    print("[daemon] is another daemon already running? Try `python rpc.py stop`.")
    raise
srv.listen(4)
srv.settimeout(0.5)  # short accept timeout so Ctrl+C is responsive
set_state("idle", "")
_daemon_start_mono = time.time()
append_rpc_log("%s daemon-start pid=%s" % (time.strftime("%H:%M:%S"), os.getpid()))
print("[daemon] listening. Ctrl+C in this console to stop.")


def _log_exit(reason, with_traceback=True):
    """Single choke point for every daemon exit path. Records the reason,
    UPTIME (how long this instance survived -- the daemon self-terminates
    ~5-11 min after start via an IDE-injected KeyboardInterrupt, and we
    want to see that duration), pid, and -- crucially -- the traceback.
    For a KeyboardInterrupt the traceback pins WHICH blocking call was
    interrupted (accept / recv / sleep), which tells us whether the IDE
    injected it externally vs an internal fault. Safe to call outside an
    except block: format_exc() then yields 'NoneType: None' and is skipped."""
    up = time.time() - _daemon_start_mono
    line = "%s daemon-exit reason=%s uptime=%.1fs pid=%s" % (
        time.strftime("%H:%M:%S"), reason, up, os.getpid())
    append_rpc_log(line)
    print("[daemon] EXIT: " + line)
    if with_traceback:
        tb = traceback.format_exc()
        if tb and "NoneType: None" not in tb:
            append_rpc_log("%s daemon-exit-traceback:\n%s" % (
                time.strftime("%H:%M:%S"), tb))

stop_requested = False
# Accept-error throttle: a broken listening socket (e.g. WSAEINVAL 10022 on
# Windows) used to make accept() raise immediately every iteration, spinning
# at 100% CPU and flooding daemon.rpc.log with 100k+ identical lines until
# CODESYS crashed. Sleep + bound the retries so a wedged socket exits the
# daemon cleanly instead of taking the IDE down with it.
#
# Self-heal (added 2026-04-28): WSAEINVAL on accept() reliably appears in this
# environment after dense back-to-back client connections (e.g. pytest runs
# that fire many rpc.py exec calls). Empirically, closing and rebinding the
# listening socket recovers the daemon without requiring a manual restart in
# the Scripting Console. Other accept errors still fall through to the
# bounded-retry exit path -- only WSAEINVAL is treated as recoverable.
ACCEPT_ERR_SLEEP   = 1.0   # seconds between retries when accept() raises
ACCEPT_ERR_MAX     = 5     # consecutive errors before giving up
accept_err_streak  = 0
WSAEINVAL          = 10022


def _rebind_listen_socket():
    """Close + recreate the daemon's listening socket. Returns the new
    socket on success, or raises if rebind fails (caller falls through to
    the streak-exit path)."""
    global srv
    try: srv.close()
    except Exception: pass
    new_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    new_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    new_srv.bind((HOST, PORT))
    new_srv.listen(4)
    new_srv.settimeout(0.5)
    srv = new_srv
    return new_srv
try:
    while not stop_requested:
        try:
            client, _addr = srv.accept()
            accept_err_streak = 0
        except socket.timeout:
            continue
        except KeyboardInterrupt:
            print("[daemon] stopped by Ctrl+C")
            _log_exit("KeyboardInterrupt-on-accept")
            break
        except Exception as ex:
            accept_err_streak += 1
            is_wsaeinval = (
                len(getattr(ex, "args", ())) >= 1
                and isinstance(ex.args[0], int)
                and ex.args[0] == WSAEINVAL
            )
            # Log only the first occurrence of a streak (avoid log flood) plus
            # the final one before bailing out.
            if accept_err_streak == 1 or accept_err_streak >= ACCEPT_ERR_MAX:
                append_rpc_log("%s accept-exception (streak=%d wsaeinval=%s): %s" % (
                    time.strftime("%H:%M:%S"), accept_err_streak,
                    is_wsaeinval, repr(ex)[:200]))
            # Self-heal on WSAEINVAL: rebind the listening socket once per
            # streak. If rebind succeeds, reset the streak and resume; if it
            # raises, fall through to the bounded-retry exit.
            if is_wsaeinval:
                try:
                    srv = _rebind_listen_socket()
                    append_rpc_log("%s accept-rebound after WSAEINVAL streak=%d"
                                   % (time.strftime("%H:%M:%S"), accept_err_streak))
                    accept_err_streak = 0
                    continue
                except Exception as rebind_ex:
                    append_rpc_log("%s rebind-failed: %s" % (
                        time.strftime("%H:%M:%S"), repr(rebind_ex)[:200]))
                    # fall through to streak-exit
            if accept_err_streak >= ACCEPT_ERR_MAX:
                _log_exit("accept-error-streak")
                print("[daemon] giving up after %d accept errors; restart me"
                      % ACCEPT_ERR_MAX)
                break
            time.sleep(ACCEPT_ERR_SLEEP)
            continue
        try:
            stop_requested = handle_client(client)
        except KeyboardInterrupt:
            _log_exit("KeyboardInterrupt-in-handler")
            raise
        except Exception as ex:
            # Don't let a handler exception kill the loop.
            append_rpc_log("%s handler-exception: %s" % (
                time.strftime("%H:%M:%S"), repr(ex)[:200]))
            traceback.print_exc()
        if stop_requested:
            _log_exit("stop-cmd", with_traceback=False)
except KeyboardInterrupt:
    _log_exit("KeyboardInterrupt-outer")
    print("[daemon] KeyboardInterrupt")
except BaseException as ex:
    # Any non-KeyboardInterrupt escape: reason = the exception class, full
    # traceback to the log. The modal "ArgumentNullException (param del)"
    # dialog truncates its traceback; the real frames land here.
    _log_exit("fatal:" + type(ex).__name__)
finally:
    # Signal + join the heartbeat thread BEFORE returning so IronPython
    # doesn't have to abort a live thread during interpreter teardown
    # (the suspected source of the shutdown ArgumentNullException dialog).
    try:
        hb_stop.set()
        hb.join(3.0)
    except Exception:
        pass
    try: srv.close()
    except Exception: pass
    set_state("stopped", "")
    try:
        _final_up = time.time() - _daemon_start_mono
    except NameError:
        _final_up = -1.0
    append_rpc_log("%s daemon-stop pid=%s uptime=%.1fs" % (
        time.strftime("%H:%M:%S"), os.getpid(), _final_up))
    print("[daemon] stopped.")
