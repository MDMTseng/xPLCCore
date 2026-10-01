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
sys.path.insert(0, os.path.join(_HERE, "sim"))
_argv, sys.argv = sys.argv, sys.argv[:1]     # run_virtual parses nothing at import
import run_virtual as _rv  # noqa: E402
sys.argv = _argv

PY = sys.executable
RPC = os.path.join(REPO, "codesys_scripts", "rpc.py")

# GA_EV events the host may post (DrainHostPackets): the visu codes differ.
EV_POWER_ON, EV_GROUP_ENABLE, EV_HOME_GO, EV_HOME_SKIP, EV_RESET = 2, 4, 6, 7, 8
SYNC_OFFSET_MAX = 50


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---- the link ---------------------------------------------------------

def push(action, payload=None, timeout=30.0):
    return _rv.push(action, payload or {}, timeout=timeout)


def plc(pkt, timeout_ms=5000):
    """One packet through the UI; returns the reply (NAK raises). A dropped
    UI link ("PLC not connected") is re-established once and retried."""
    try:
        return push("plc_send", {"pkt": pkt, "timeoutMs": timeout_ms}, timeout=timeout_ms / 1000 + 5)
    except RuntimeError as e:
        if "not connected" not in str(e):
            raise
        reconnect()
        return push("plc_send", {"pkt": pkt, "timeoutMs": timeout_ms}, timeout=timeout_ms / 1000 + 5)


def sys_cmd(cmd, timeout_ms=5000, **kw):
    return plc(dict(kw, type="SYS", cmd=cmd), timeout_ms)


def m_cmd(cmd, timeout_ms=5000, **kw):
    return plc(dict(kw, type="M", cmd=cmd), timeout_ms)


def reconnect(tries=20):
    """Make sure the UI's PLC link is up (it does not always come back by
    itself after a download)."""
    for _ in range(tries):
        try:
            push("plc_send", {"pkt": {"type": "SYS", "cmd": "PING"}, "timeoutMs": 3000}, timeout=8)
            return
        except Exception:
            try:
                push("disconnect_tcp", timeout=20)
                time.sleep(2)
                push("connect_tcp", timeout=20)
                time.sleep(2)
            except Exception:
                time.sleep(3)
    raise SystemExit("the UI's link to the PLC did not come back")


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
    rpc_write("GVL.AxisSimConfigMask", 0 if real else 7)
    want = 0 if real else 7
    # The PLC refuses a new mask while it still applies the last one
    # ("sim_apply_busy", e.g. right after a download or a mode change).
    for i in range(30):
        try:
            sys_cmd("SET_AXIS_SIM", mask=want)
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
    for v in ("xConfigFinished", "LastMessage"):
        lines = rpc("read", "IoConfig_Globals.EtherCAT_Master_SoftMotion." + v, timeout=120)
        out[v] = lines[-1] if lines else "?"
    rpc("logout", timeout=60)
    return out


def save_plc_log():
    """Read the PLC runtime log (500 entries) into
    codesys_scripts/jobs/plc_log_PlcLog.txt and return its path."""
    rpc("exec", "--readonly", "--file", "jobs/templates/read_plc_log.py", timeout=200)
    return os.path.join(REPO, "codesys_scripts", "jobs", "plc_log_PlcLog.txt")


def safe_install(read_log=True):
    """Download the project with every safety step: (PLC log) -> delta
    powered off, checked -> rpc install --on-site -> relink the UI ->
    EtherCAT up. The delta comes back virtual (project default)."""
    if read_log:
        log("PLC log saved:", save_plc_log())
    drives_off()
    lines = rpc("install", "--on-site", timeout=900)
    done = [l for l in lines if "download done" in l]
    log("install:", done[0] if done else "\n".join(lines[-5:]))
    if not done:
        raise SystemExit("install did not complete")
    reconnect()
    time.sleep(3)
    ec = ethercat_state()
    log("EtherCAT:", ec)
    if "TRUE" not in ec.get("xConfigFinished", ""):
        raise SystemExit("EtherCAT not up after the download -- save the PLC log before retrying")
    return ec
