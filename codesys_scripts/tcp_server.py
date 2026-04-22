# -*- coding: ascii -*-
# tcp_server.py -- paste into CODESYS Scripting Console, OR run via
# Tools -> Scripting -> Execute Script File...
#
# Single-threaded TCP server on 127.0.0.1:9790 that exec()s the script body
# sent by each client inside this warm CODESYS session. The job's captured
# stdout+stderr is streamed back, terminated by "__DONE__ rc=<code>\n".
#
# Wire format (line-based, ASCII):
#   <-- client sends arbitrary Python (IronPython) source
#   <-- client sends a line consisting only of __EOF__
#   --> server streams captured output
#   --> server ends with a line: __DONE__ rc=<int>
#   --> server closes the connection
#
# Why single-threaded + select(): keeps the scripting-engine API on one
# thread (CODESYS scripting globals like `projects`/`system` are UI-thread
# affine). The 0.3s select timeout lets the UI stay responsive between
# accepts.

import sys
import socket
import select
import traceback

try:
    from cStringIO import StringIO
except ImportError:
    from StringIO import StringIO

# Kill any line-level tracer that CODESYS's Execute-Script-File mode
# attaches -- otherwise every iteration of the poll loop logs its source
# lines into Script Messages and the pane fills up (and the UI feels
# stuck).
try:
    sys.settrace(None)
except Exception:
    pass
try:
    system.trace = False
except Exception:
    pass

HOST = "127.0.0.1"
PORT = 9790
EOF_LINE   = "__EOF__"
DONE_LINE  = "__DONE__"
POLL_SEC   = 0.3

DEFAULT_PROJECT = r"C:\Users\X1\Desktop\XPack2_codesys\PackerX.project"

# ---- preload project so the first request is warm ----
try:
    if projects.primary is None:
        print("[server] opening project: " + DEFAULT_PROJECT)
        projects.open(DEFAULT_PROJECT)
        print("[server] project opened")
    else:
        print("[server] reusing already-open project: " + projects.primary.path)
except Exception:
    print("[server] project preload failed:")
    traceback.print_exc()

# ---- socket setup ----
# If an earlier run left a socket bound (abort didn't close it), try to
# close it now. We stash our srv on a well-known attribute so a re-run
# can find it.
_prev = getattr(system, "_tcp_srv_socket", None)
if _prev is not None:
    try:
        _prev.close()
        print("[server] closed prior socket from previous run")
    except Exception:
        pass
    try:
        system._tcp_srv_socket = None
    except Exception:
        pass

srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    srv.bind((HOST, PORT))
except Exception as ex:
    print("[server] bind failed: " + str(ex))
    print("[server] port %d is still held; close the IDE and reopen." % PORT)
    raise
srv.listen(1)
srv.setblocking(False)
try:
    system._tcp_srv_socket = srv
except Exception:
    pass
print("[server] listening on %s:%d  (timeout-poll %.1fs)" % (HOST, PORT, POLL_SEC))
print("[server] Ctrl+C in the console to stop. Re-run this script to restart.")


def _read_script(conn):
    """Read bytes from conn until we see a line that equals EOF_LINE."""
    buf_all = []
    pending = ""
    while True:
        chunk = conn.recv(4096)
        if not chunk:
            break
        try:
            s = chunk.decode("utf-8", "replace")
        except Exception:
            s = str(chunk)
        pending += s
        # Peel off full lines to look for EOF marker.
        while "\n" in pending:
            nl = pending.index("\n")
            line = pending[:nl]
            rest = pending[nl+1:]
            if line.rstrip("\r") == EOF_LINE:
                return "".join(buf_all)
            buf_all.append(line + "\n")
            pending = rest
    # Connection closed before EOF marker; treat whatever we got as the body.
    buf_all.append(pending)
    return "".join(buf_all)


def _handle(conn, addr):
    t_tag = "%s:%d" % addr
    src = _read_script(conn)
    if not src.strip():
        conn.sendall("__DONE__ rc=2\n")
        return
    # Capture stdout/stderr of the job
    old_out, old_err = sys.stdout, sys.stderr
    buf = StringIO()
    sys.stdout = buf
    sys.stderr = buf
    rc = 0
    try:
        code = compile(src, "<tcp:%s>" % t_tag, "exec")
        g = dict(globals())
        g["__name__"] = "__main__"
        g["__file__"] = "<tcp>"
        exec(code, g)
    except SystemExit as ex:
        try:
            rc = int(ex.code) if ex.code is not None else 0
        except Exception:
            rc = 1
    except Exception:
        rc = 1
        buf.write("\n__EXC__\n")
        buf.write(traceback.format_exc())
    finally:
        sys.stdout, sys.stderr = old_out, old_err

    out = buf.getvalue()
    try:
        conn.sendall(out.encode("utf-8", "replace") if isinstance(out, unicode) else out)
    except NameError:
        # Py3-style branch (shouldn't hit on IronPython 2.7, defensive only)
        conn.sendall(out.encode("utf-8"))
    conn.sendall("\n%s rc=%d\n" % (DONE_LINE, rc))
    # Explicit half-close so the client's recv returns EOF promptly on
    # Windows; plain close() can linger.
    try:
        conn.shutdown(socket.SHUT_WR)
    except Exception:
        pass
    print("[server] %s handled, rc=%d, out=%d bytes" % (t_tag, rc, len(out)))


try:
    while True:
        try:
            ready, _, _ = select.select([srv], [], [], POLL_SEC)
        except Exception:
            # Rare: select can raise on signal. Ignore and loop.
            continue
        if not ready:
            continue
        conn, addr = srv.accept()
        try:
            conn.settimeout(30.0)
            _handle(conn, addr)
        except Exception:
            traceback.print_exc()
        finally:
            try:
                conn.close()
            except Exception:
                pass
except KeyboardInterrupt:
    print("[server] stopped by Ctrl+C.")
finally:
    try:
        srv.close()
    except Exception:
        pass
