"""One command for the all-virtual scene: PLC in simulation, mocks for vision
and the feeder, the UI driven through the remote harness.

    python tools/sim/run_virtual.py [--plc 192.168.1.70] [--cycles 20]
                                    [--no-ui-start] [--home skip|go]

Order matters and is encoded here:

  1. PLC: simulated digital inputs on (GVL.SimDigitalInputEnable), through
     the CODESYS daemon. Requires the delta arms to be virtual -- checked.
  2. remote_harness.py (:8127), vision_mock.py (:7950, polls PLC :8126/v).
  3. The standalone UI with XPLC_HARNESS=1.
  4. Through the harness: connect the PLC, *then* vision (vision callbacks
     only register while the PLC socket exists), then the MOCK feeder;
     bring the motion FSM to Ready; press RUN.
  5. Watch: running state, vision mock log, until --cycles tape checks or
     an error.

Everything started here is stopped on exit (Ctrl+C included). Logs go to
standalone/data/sim_logs/.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPTS = os.path.join(REPO, "codesys_scripts")
sys.path.insert(0, SCRIPTS)
import rpc  # noqa: E402  (daemon client)

HARNESS = "http://127.0.0.1:8127"
LOGS = os.path.join(REPO, "standalone", "data", "sim_logs")
NODE_DIR = r"C:\Program Files\nodejs"


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def daemon(req, timeout=60):
    r = rpc.call(req, timeout)
    for _ in range(3):
        if r.get("ok") or "Not logged in" not in str(r.get("error", "")):
            break
        # Right after a write the daemon's session sometimes reports "Not
        # logged in" for a second or so; drop it, wait, retry.
        rpc.call({"cmd": "logout"}, timeout)
        time.sleep(1.0)
        r = rpc.call(req, timeout)
    if not r.get("ok"):
        raise RuntimeError("daemon %s failed: %s" % (req.get("cmd"), r))
    return r


def push(action, payload=None, timeout=30.0):
    body = json.dumps({"action": action, "payload": payload or {},
                       "wait_for_result": True, "timeout": timeout}).encode()
    req = urllib.request.Request(HARNESS + "/push", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout + 5) as r:
        res = json.loads(r.read().decode())
    res = res.get("result", res)   # remote_harness wraps it: {instr_id, result:{ok, value}}
    if not res.get("ok"):
        raise RuntimeError("%s: %s" % (action, res.get("err", res)))
    return res.get("value")


def wait_for(what, fn, timeout=30.0, period=0.5):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = fn()
            if last:
                return last
        except Exception as e:
            last = e
        time.sleep(period)
    raise RuntimeError("timed out waiting for %s (last: %r)" % (what, last))


# E_RobotEvent values the host may post (protocol.md, SYS GA_EV).
EV_NONE, EV_POWER_ON, EV_GROUP_ENABLE, EV_HOME_GO, EV_HOME_GO_FORCE_SKIP, EV_RESET = 0, 2, 4, 6, 7, 8


def ga_ev(ev):
    reply = push("ga_ev", {"ev": ev}, timeout=10)["reply"]
    return reply.get("st_str"), reply


def bring_to_ready(home):
    """Walk the motion FSM to Ready.

    The UI's init_plc_motion always sends HOME_GO. In the virtual scene the
    delta arms are simulated, their home switches never trip, and FB_Homing
    times out into Error after 30 s. So by default this skips homing with
    EV_HOME_GO_FORCE_SKIP at GroupEnabled -- the PLC allows that only while
    the delta trio is simulated (AxisSimMask & 7 = 7). --home go does real
    homing instead.
    """
    end = time.time() + 120
    last = None
    while time.time() < end:
        st, reply = ga_ev(EV_NONE)
        if st != last:
            log("FSM:", st, reply.get("err_src") or "")
            last = st
        if st == "Ready":
            break
        ev = {"UnInited": EV_POWER_ON, "Powered": EV_GROUP_ENABLE, "Error": EV_RESET,
              "GroupEnabled": EV_HOME_GO if home == "go" else EV_HOME_GO_FORCE_SKIP}.get(st)
        if ev is not None:
            ga_ev(ev)
        time.sleep(0.5)
    else:
        raise RuntimeError("FSM did not reach Ready (last %s)" % last)
    # What init_plc_motion does once Ready: coordinate system, then a move to
    # the start pose (A swings to 90 and back -- EAXIS_A is real and cleared).
    for pkt in ({"type": "M", "cmd": "SetCoord1"},
                {"type": "M", "cmd": "G1", "X": 0, "Y": 0, "Z": 10, "A": 90},
                {"type": "M", "cmd": "G1", "A": 0}):
        log("send", pkt["cmd"], push("send_tcp_msgpack", {"data": pkt}, timeout=10))


def plc_prepare():
    # Refuse unless the delta arms are virtual: the virtual scene must never
    # move them. EAXIS_A (the A rotation) and the reel are cleared to move.
    for ax in ("EAxis0", "EAxis1", "EAxis2"):
        v = daemon({"cmd": "read", "symbol": "IoConfig_Globals.%s.bVirtual" % ax})["value"]
        if v.upper() != "TRUE":
            raise SystemExit("ABORT: %s is not virtual (%s)" % (ax, v))
    daemon({"cmd": "write", "symbol": "GVL.SimDigitalInputEnable", "value": "TRUE"})
    # Read back only after a pause: an immediate read after the write came
    # back "Not logged in" (2026-09-24); a few hundred ms later it is fine.
    time.sleep(0.5)
    got = daemon({"cmd": "read", "symbol": "GVL.SimDigitalInputEnable"})["value"]
    daemon({"cmd": "logout"})
    log("PLC: delta arms virtual, simulated inputs", got)


def start(name, argv, env=None):
    os.makedirs(LOGS, exist_ok=True)
    f = open(os.path.join(LOGS, name + ".log"), "w", encoding="utf-8")
    p = subprocess.Popen(argv, cwd=REPO, stdout=f, stderr=subprocess.STDOUT,
                         env=env or os.environ.copy())
    log("started", name, "pid", p.pid)
    return p


def tail(name, n=5):
    try:
        with open(os.path.join(LOGS, name + ".log"), encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()[-n:]
    except OSError:
        return []


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--plc", default="192.168.1.70")
    ap.add_argument("--cycles", type=int, default=20, help="stop after this many tape checks")
    ap.add_argument("--home", choices=("go", "skip"), default="skip",
                    help="'skip' (default): EV_HOME_GO_FORCE_SKIP, allowed while the delta trio is simulated; 'go': real homing")
    ap.add_argument("--no-ui-start", action="store_true", help="UI already running with XPLC_HARNESS=1")
    a = ap.parse_args()

    procs = []
    try:
        plc_prepare()
        procs.append(start("remote_harness", [sys.executable, os.path.join(SCRIPTS, "internals", "remote_harness.py")]))
        procs.append(start("vision_mock", [sys.executable, os.path.join(REPO, "tools", "sim", "vision_mock.py"),
                                           "--plc", "%s:8126" % a.plc]))
        if not a.no_ui_start:
            env = os.environ.copy()
            env["PATH"] = NODE_DIR + os.pathsep + env.get("PATH", "")
            env["XPLC_HARNESS"] = "1"
            env["XPLC_CONSOLE"] = "1"
            procs.append(start("ui", [os.path.join(NODE_DIR, "node.exe"), os.path.join(REPO, "standalone", "run.cjs")], env))

        wait_for("UI to poll the harness", lambda: push("ping", timeout=5), timeout=90)
        log("UI up:", push("get_state"))

        push("connect_tcp", {"host": a.plc, "port": 8125})
        wait_for("PLC link", lambda: push("get_state")["tcpConnected"], timeout=20)
        push("connect_vision", {"host": "localhost", "port": 7950})
        wait_for("vision link", lambda: push("get_state")["visionStatus"] == 2, timeout=20)
        push("connect_feeder", {"port": "MOCK"})
        wait_for("feeder bridge", lambda: push("get_state")["feederStatus"] == 2, timeout=20)
        log("links up:", push("get_state"))

        bring_to_ready(a.home)
        push("set_tab", {"tab": "Calib"}, timeout=10)
        log("plan:", push("set_plan", {"plan": [a.cycles + 5]}, timeout=10))
        log("run_cycle:", push("run_cycle", timeout=10))

        checks = 0
        seen = 0
        while checks < a.cycles:
            time.sleep(2.0)
            rs = push("get_running_state", timeout=10)
            lines = tail("vision_mock", 200)
            checks = sum(1 for l in lines if "push 134500" in l)
            if len(lines) != seen:
                seen = len(lines)
            log("running=%s state=%r err=%r tape_checks=%d pack=%r" % (
                rs.get("isRunning"), rs.get("runningState"), rs.get("currentError"),
                checks, rs.get("packInfoString")))
            if rs.get("currentError") or not rs.get("isRunning"):
                log("stopped or error; vision mock tail:")
                for l in tail("vision_mock", 8):
                    print("   ", l)
                break
        push("stop_cycle", timeout=15)
        log("stop_cycle sent")
    finally:
        for p in reversed(procs):
            try:
                p.terminate()
            except Exception:
                pass
        log("stopped helpers; logs in", LOGS)


if __name__ == "__main__":
    main()
