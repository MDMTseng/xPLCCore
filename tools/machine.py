"""Shared machine access for the tools: the PLC through the standalone UI's
harness link, the FSM, the delta's real/virtual mode, and the safety checks
every tool must make the same way.

The link: the standalone UI (XPLC_HARNESS=1 node standalone/run.cjs) holds
the PLC's TCP connection; `plc()` sends one packet through its `plc_send`
harness action (~1 s round trip). Long streams go through `plc_stream_*`
(MotorTestPage).

Safety rules (memory: machine-motion-safety, plc-download-pitfalls):
  - The real delta moves only with the owner's OK for that run:
    `require_owner_ok()` (a --owner-ok flag or XPLC_OWNER_OK=1).
  - Download only with every delta drive powered off: `drives_off()`
    before `safe_install()`; `safe_install()` checks it again.
  - Real/virtual goes through `set_delta()`, which writes both
    GVL.AxisSimConfigMask (re-applied at every UnInited entry) and
    SET_AXIS_SIM, and reads the result back.
  - SyncOffset stays <= 50 (>= 60 wedged the PLC's EtherCAT layer).
  - Before retrying after a bus problem, save the PLC log (`save_plc_log()`,
    500 entries only).
"""

import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(_HERE)
import xplc  # noqa: E402

# The link: one xplc.Machine through the UI's PLC link. Its typed errors
# (xplc.PlcError, a RuntimeError) carry the PLC's err text, so the callers
# that match on "busy" / "block_timeout" keep working (2026-10-05).
_M = xplc.Machine(xplc.UiRelay())

PY = sys.executable
RPC = os.path.join(REPO, "codesys_scripts", "rpc.py")

# GA_EV events the host may post (DrainHostPackets): the visu codes differ.
EV_POWER_ON, EV_GROUP_ENABLE, EV_HOME_GO, EV_HOME_SKIP, EV_RESET = 2, 4, 6, 7, 8
SYNC_OFFSET_MAX = 50


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---- the link ---------------------------------------------------------

def push(action, payload=None, timeout=30.0):
    """Any harness action through the UI (streams, connect/disconnect)."""
    return _M.t.push(action, payload or {}, timeout=timeout)


def plc(pkt, timeout_ms=5000):
    """One packet through the UI; returns the reply (a NAK raises xplc.Nak).
    A dropped UI link is re-established once and the packet sent again."""
    return _M.send(pkt, timeout_ms)


def sys_cmd(cmd, timeout_ms=5000, **kw):
    return _M.sys(cmd, timeout_ms, **kw)


def m_cmd(cmd, timeout_ms=5000, **kw):
    return _M.m(cmd, timeout_ms, **kw)


def reconnect(tries=20):
    """Make sure the UI's PLC link is up (it does not always come back by
    itself after a download)."""
    _M.t.reconnect(tries)


SUPERVISOR = os.path.join(REPO, "codesys_scripts", "supervisor.py")


def restart_daemon():
    """The daemon retires after its 4 h budget ("daemon not reachable"):
    kill the IDE and start it again (it saves after every job)."""
    subprocess.run([PY, SUPERVISOR, "kill", "--yes"], cwd=REPO, capture_output=True, timeout=120)
    for _ in range(15):
        r = subprocess.run([PY, SUPERVISOR, "ps"], cwd=REPO, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
        if "No CODESYS" in (r.stdout or ""):
            break
        time.sleep(2)
    subprocess.run([PY, SUPERVISOR, "start"], cwd=REPO, capture_output=True, timeout=600)
    log("CODESYS daemon restarted")


def rpc(*args, timeout=600):
    """Run codesys_scripts/rpc.py (the CODESYS daemon); returns output lines.

    The daemon retires itself after its time / job budget (config
    session_max_seconds, default 4 h; session_max_jobs, 150): it answers the
    job, then saves, logs out and exits. The call after that used to hit the
    exiting daemon (ConnectionResetError; 2026-10-01 an install failed that
    way). Now:
    - "[budget] session retiring" in the reply: wait for the exit, restart
      the daemon before returning;
    - not reachable / connection reset / empty reply: restart it and retry
      the call once -- except an install, which is reported, not repeated
      (never risk a second download over a half-done one)."""
    lines = _rpc(*args, timeout=timeout)
    dead = any(s in l for l in lines for s in (
        "daemon not reachable", "start it with: rpc.py daemon-start",
        "ConnectionResetError", "empty reply from daemon"))
    if dead:
        restart_daemon()
        if args and args[0] == "install":
            return lines + ["daemon restarted after a lost connection; install NOT retried"]
        return _rpc(*args, timeout=timeout)
    if any("[budget] session retiring" in l for l in lines):
        log("CODESYS daemon retiring (budget): waiting for it to exit, then restarting it")
        for _ in range(30):
            if any("not reachable" in l for l in _rpc("ping", timeout=60)):
                break
            time.sleep(2)
        restart_daemon()
    return lines


def _rpc(*args, timeout=600):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([PY, RPC] + list(args), cwd=REPO, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout, env=env)
    return ((r.stdout or "") + (r.stderr or "")).strip().splitlines()


def run_tool(name, *args, timeout=600):
    """Run another script from tools/; returns its output lines."""
    r = subprocess.run([PY, os.path.join(_HERE, name)] + list(args), cwd=REPO, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout,
                       env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    return ((r.stdout or "") + (r.stderr or "")).strip().splitlines()


def rpc_write(symbol, value):
    out = rpc("write", symbol, str(value), timeout=120)
    rpc("logout", timeout=60)
    return out


# ---- FSM --------------------------------------------------------------

def fsm():
    """(state name, reply) of the axis-group FSM."""
    r = sys_cmd("GA_EV", ev=0)
    return r["st_str"], r


def fsm_to(want, home=True, timeout=120):
    """Walk the FSM to UnInited / Powered / Ready. Ready homes the real
    delta (EV_HOME_GO) or, when home=False, skips homing -- only valid while
    the delta is virtual (the PLC refuses it for real axes)."""
    end = time.time() + timeout
    while time.time() < end:
        st, r = fsm()
        if st == want:
            return r
        if st == "Error" and want != "UnInited":
            ev = EV_RESET
        elif want == "UnInited":
            ev = EV_RESET
        elif want == "Powered":
            ev = {"UnInited": EV_POWER_ON}.get(st, EV_RESET if st not in ("Powering",) else None)
        else:
            ev = {"UnInited": EV_POWER_ON, "Powered": EV_GROUP_ENABLE,
                  "GroupEnabled": EV_HOME_GO if home else EV_HOME_SKIP}.get(st)
        if ev is not None:
            try:
                sys_cmd("GA_EV", ev=ev)
            except Exception:
                pass
        time.sleep(0.5)
    st, r = fsm()
    raise SystemExit("FSM did not reach %s (%s, %s %s)" % (want, st, r.get("err_src"), r.get("err_id")))


# ---- delta mode and power ----------------------------------------------

def delta_mask():
    return sys_cmd("GET_MACHINE_STATE").get("axes_sim_mask", -1) & 7


def is_delta_virtual():
    return delta_mask() == 7


def axis_states():
    return [sys_cmd("AXIS_INFO", axis=k)["st"] for k in range(3)]


def drives_off():
    """FSM to UnInited and check every delta axis is power_off (st 0)."""
    fsm_to("UnInited")
    st = axis_states()
    if st != [0, 0, 0]:
        raise SystemExit("delta not powered off: axis states %s" % st)
    return st


def set_delta(real):
    """Real or virtual delta for this run of the PLC (a download or restart
    brings back the project's virtual delta). Powers the delta off first."""
    drives_off()
    want = 0 if real else 7
    # SYS DELTA_MODE (2026-10-02): the delta trio only -- the reel's bit is
    # left alone (with its slave bypassed, set_slaves_bypass.py, the reel
    # must stay virtual). It replaces writing GVL.AxisSimConfigMask through
    # the IDE plus SET_AXIS_SIM. The PLC refuses a new mask while it still
    # applies the last one ("sim_apply_busy").
    pkt = {"real": 1, "site_clear": 1} if real else {"real": 0}
    for i in range(30):
        try:
            sys_cmd("DELTA_MODE", **pkt)
            break
        except RuntimeError as e:
            if "sim_apply_busy" not in str(e) or i == 29:
                raise
            time.sleep(1)
    for _ in range(60):
        if delta_mask() == want:
            return
        time.sleep(0.5)
    raise SystemExit("delta mode did not change (mask %s)" % delta_mask())


_drive_checked = set()


def drive_sdo(station, index, sub=0, size=4, value=None):
    """One SDO read (value) or write (value given) on a slave through the
    PLC (SYS DRV_SDO / DRV_SDO_RESULT). The first write to a station checks
    its identity 0x1018:01 against the Delta vendor id first: a slave
    reorder (2026-10-02) moved the station addresses and the old 1003 became
    the reel, so no drive parameter is ever written blind. Raises on an
    SDO error or when no result comes back."""
    import topology as tp
    if value is not None and station not in _drive_checked:
        vendor = drive_sdo(station, 0x1018, sub=1)
        if vendor != tp.DELTA_VENDOR:
            raise SystemExit("station %d is not a Delta drive (vendor 0x%X): no write" % (station, vendor))
        _drive_checked.add(station)
    pkt = {"station": station, "index": index, "sub": sub, "size": size}
    if value is not None:
        pkt.update(value=int(value), write=1)
    for _ in range(50):
        try:
            seq = sys_cmd("DRV_SDO", **pkt)["seq_req"]
            break
        except RuntimeError as e:
            if "busy" not in str(e):
                raise
            time.sleep(0.2)
    else:
        raise SystemExit("SDO channel stays busy")
    for _ in range(50):
        r = sys_cmd("DRV_SDO_RESULT")
        if not r["active"] and r["seq_res"] == seq:
            if not r["ok"]:
                raise SystemExit("SDO %s 0x%04X:%d on %d failed: error %s" % (
                    "write" if value is not None else "read", index, sub, station, r["sdo_err"]))
            return r["value"]
        time.sleep(0.2)
    raise SystemExit("SDO 0x%04X:%d on %d: no result" % (index, sub, station))


def require_owner_ok(flag=False):
    """The real delta moves only with the owner's OK for this run."""
    if flag or os.environ.get("XPLC_OWNER_OK") == "1":
        return
    raise SystemExit("REFUSED: this moves the real delta. Get the owner's OK for this run, "
                     "then pass --owner-ok (or set XPLC_OWNER_OK=1).")


# ---- bus health, PLC log, deploy -----------------------------------------

def bus_up():
    """(ok, detail): the EtherCAT master finished its startup (xConfigFinished,
    via the daemon). The EtherCAT task keeps cycling with a dead bus, so
    cycle counts or joint states from the PLC do not tell."""
    ec = ethercat_state()
    return "TRUE" in ec.get("xConfigFinished", ""), ec


def ethercat_state():
    """From the CODESYS daemon: master xConfigFinished and LastMessage."""
    out = {}
    # A read right after an install or restart_app answers "Application not
    # logged in" until the session is logged out once (2026-10-02).
    rpc("logout", timeout=60)
    for v in ("xConfigFinished", "LastMessage"):
        lines = rpc("read", "IoConfig_Globals.EtherCAT_Master_SoftMotion." + v, timeout=120)
        out[v] = lines[-1] if lines else "?"
    rpc("logout", timeout=60)
    return out


def save_plc_log():
    """Read the PLC runtime log (500 entries) into
    codesys_scripts/jobs/plc_log_PlcLog.txt and return its path."""
    # Absolute: rpc runs with cwd=REPO, so the old relative path was never
    # found and every caller got the stale file back (2026-10-05).
    job = os.path.join(REPO, "codesys_scripts", "jobs", "templates", "read_plc_log.py")
    lines = rpc("exec", "--readonly", "--file", job, timeout=200)
    path = os.path.join(REPO, "codesys_scripts", "jobs", "plc_log_PlcLog.txt")
    # 2026-10-05: a failed job (it died on a non-ASCII log message) left the
    # old file in place and this returned it as if it were fresh.
    if not any("written:" in l for l in lines):
        raise SystemExit("PLC log not read:\n" + "\n".join(lines[-6:]))
    return path


TEMPLATES = os.path.join(REPO, "codesys_scripts", "jobs", "templates")


def run_job(template, label, plc="keep", params=None, timeout=600):
    """Run jobs/templates/<template> through the daemon with `params`
    (name -> value) set after its `import time` line; returns output lines."""
    with open(os.path.join(TEMPLATES, template), encoding="utf-8") as f:
        code = f.read()
    if params:
        head = "".join("%s = %r\n" % kv for kv in params.items())
        code = code.replace("import time\n", "import time\n" + head, 1)
    tmp = os.path.join(REPO, "codesys_scripts", "jobs", "_job_%s.py" % label)
    with open(tmp, "w", encoding="ascii") as f:
        f.write(code)
    return rpc("exec", "--plc", plc, "--label", label, "--file", tmp, timeout=timeout)


def download_and_start(max_download=45.0, settle=5.0, bus_wait=30.0, say=None):
    """The one download implementation (tools/deploy.py step 7 and
    safe_install): download, wait `settle` s, start, wait for the EtherCAT
    master in the same session (jobs/templates/download_wait_start.py). A
    download slower than `max_download` s is NOT started (2026-10-05: the
    two downloads after which EtherCAT never came up took 83.5 s and 92 s;
    good ones 15-32 s). Raises SystemExit with the machine's state on any
    failure; never downloads twice."""
    say = say or log
    lines = run_job("download_wait_start.py", "download", "download",
                    {"MAX_DOWNLOAD_S": max_download, "SETTLE_S": settle, "BUS_WAIT_S": bus_wait}, timeout=900)
    for l in lines:
        if any(k in l for k in ("download done", "post-start", "ethercat:", "DOWNLOAD SLOW", "EXCEPTION", "Error")):
            say("   |", l)
    if any("DOWNLOAD SLOW" in l for l in lines):
        raise SystemExit("download slower than %.0f s: NOT started (new code on the PLC, app stopped). "
                         "Ask the owner before anything else." % max_download)
    if not any("download done" in l for l in lines):
        raise SystemExit("download did not complete:\n" + "\n".join(lines[-6:]))
    if not any("post-start state:" in l and "run" in l.lower() for l in lines):
        raise SystemExit("application did not start:\n" + "\n".join(lines[-6:]))
    if not any("ethercat: UP" in l for l in lines):
        raise SystemExit("EtherCAT did not come up after the start. Save the PLC log; do NOT download "
                         "again without the owner.")
    try:
        sys.path.insert(0, os.path.join(REPO, "codesys_scripts"))
        import layout_check
        layout_check.save_baseline(layout_check.scan_disk(), "download")
    except Exception as e:
        say("layout baseline not saved: %s" % e)
    return lines


def save_incident(tag, extra=None):
    """Evidence before any recovery step (2026-10-05 review): the PLC log
    (500 entries, overwritten by a re-download), EC_STATS, the machine
    state and `extra`, into codesys_scripts/jobs/incidents/<time>_<tag>/.
    Returns the folder; each part is best effort."""
    import json
    import shutil
    d = os.path.join(REPO, "codesys_scripts", "jobs", "incidents", time.strftime("%Y%m%d-%H%M%S_") + tag)
    os.makedirs(d, exist_ok=True)
    notes = {}
    try:
        shutil.copy(save_plc_log(), os.path.join(d, "PlcLog.txt"))
    except BaseException as e:      # save_plc_log raises SystemExit
        notes["plc_log"] = str(e)
    for name, fn in (("ec_stats", lambda: sys_cmd("EC_STATS")), ("machine_state", lambda: sys_cmd("GET_MACHINE_STATE")),
                     ("ethercat_master", ethercat_state)):
        try:
            notes[name] = fn()
        except BaseException as e:
            notes[name] = "unreadable: %s" % e
    if extra:
        notes.update(extra)
    with open(os.path.join(d, "notes.json"), "w", encoding="utf-8") as f:
        json.dump(notes, f, indent=1, default=str)
    return d


def safe_install(read_log=True):
    """Download the project with every safety step: (PLC log) -> delta
    powered off, checked -> download, wait, start, EtherCAT up (one
    session, download_and_start) -> relink the UI. The delta comes back
    virtual (project default). For code changes on disk prefer
    tools/deploy.py (Error state + reset before the download)."""
    if read_log:
        log("PLC log saved:", save_plc_log())
    drives_off()
    # Nothing talks to the PLC while it changes (tools/deploy.py step 4).
    try:
        push("disconnect_tcp", timeout=20)
        time.sleep(2)
    except Exception as e:
        log("UI link not closed: %s" % e)
    try:
        download_and_start()
    finally:
        # relink also after a failure, so the state can be read
        try:
            push("connect_tcp", timeout=20)
            time.sleep(2)
        except Exception:
            pass
    reconnect()
    ec = ethercat_state()
    log("EtherCAT:", ec)
    return ec
