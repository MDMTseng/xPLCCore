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
    the shared state dict -- so it's safe to run off the IDE main thread."""
    while True:
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
        except Exception:
            pass
        time.sleep(1.0)


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
append_rpc_log("%s daemon-start pid=%s" % (time.strftime("%H:%M:%S"), os.getpid()))
print("[daemon] listening. Ctrl+C in this console to stop.")

stop_requested = False
try:
    while not stop_requested:
        try:
            client, _addr = srv.accept()
        except socket.timeout:
            continue
        except KeyboardInterrupt:
            print("[daemon] stopped by Ctrl+C")
            append_rpc_log("%s loop-exit reason=KeyboardInterrupt-on-accept"
                           % time.strftime("%H:%M:%S"))
            break
        except Exception as ex:
            # Trace anything else so we can see why the loop ended.
            append_rpc_log("%s accept-exception: %s" % (
                time.strftime("%H:%M:%S"), repr(ex)[:200]))
            traceback.print_exc()
            continue
        try:
            stop_requested = handle_client(client)
        except KeyboardInterrupt:
            append_rpc_log("%s handler-exit reason=KeyboardInterrupt"
                           % time.strftime("%H:%M:%S"))
            raise
        except Exception as ex:
            # Don't let a handler exception kill the loop.
            append_rpc_log("%s handler-exception: %s" % (
                time.strftime("%H:%M:%S"), repr(ex)[:200]))
            traceback.print_exc()
        if stop_requested:
            append_rpc_log("%s loop-exit reason=stop-cmd"
                           % time.strftime("%H:%M:%S"))
except KeyboardInterrupt:
    append_rpc_log("%s daemon-keyboardinterrupt" % time.strftime("%H:%M:%S"))
    print("[daemon] KeyboardInterrupt")
except BaseException as ex:
    append_rpc_log("%s daemon-fatal: %s" % (time.strftime("%H:%M:%S"), repr(ex)[:300]))
    traceback.print_exc()
finally:
    try: srv.close()
    except Exception: pass
    set_state("stopped", "")
    append_rpc_log("%s daemon-stop pid=%s" % (time.strftime("%H:%M:%S"), os.getpid()))
    print("[daemon] stopped.")
