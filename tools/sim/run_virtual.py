"""One command for the all-virtual scene: PLC in simulation, mocks for vision
and the feeder, the UI driven through the remote harness.

    python tools/sim/run_virtual.py [--plc 192.168.1.70] [--cycles 20]
                                    [--no-ui-start] [--home skip|go]

Order matters and is encoded here:

  1. PLC: simulated digital inputs on (GVL.SimDigitalInputEnable), through
     the CODESYS daemon. Requires the delta arms to be virtual -- checked.
  2. remote_harness.py (:8127), vision_mock.py (:7950, polls PLC :8126/v).
  3. The standalone UI with XPLC_HARNESS=1.
  4. Through the harness: connect the PLC and vision (either order,
     --vision-first), then the MOCK feeder;
     bring the motion FSM to Ready; press RUN.
  5. Watch: running state, vision mock log, until --cycles tape checks or
     an error. Meanwhile the PLC event log (event_log.py) is collected
     and the two timing-critical paths are analysed at the end
     (sim_logs/events.csv).

Everything started here is stopped on exit (Ctrl+C included). Logs go to
standalone/data/sim_logs/.

Stall detection: each phase has a limit derived from measured timings
(2026-09-24, all-virtual scene). Past it, the run stops at once with
diagnostics and exit code 2 -- no waiting out a blanket timeout.

  phase                               typical      limit
  UI answering the harness            3 s          45 s
  each link (PLC, vision, feeder)     1-5 s        20 s
  FSM to Ready                        1-6 s        40 s
  RUN -> first top-camera check       ~3 s         25 s
  between two top-camera checks       ~1 s (<=3)   12 s  (vision reply timeout is 10 s)
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPTS = os.path.join(REPO, "codesys_scripts")
sys.path.insert(0, SCRIPTS)
import rpc  # noqa: E402  (daemon client)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import event_log  # noqa: E402

HARNESS = "http://127.0.0.1:8127"

# Stall limits in seconds, see the table in the module docstring.
LIMIT_UI_UP = 45
LIMIT_LINK = 20
LIMIT_READY = 40
LIMIT_FIRST_CHECK = 25
LIMIT_BETWEEN_CHECKS = 12


# "HH:MM:SS push 134500 ..." in the vision mock log, one per tape check.
TOP_PUSH = re.compile(r"^\d\d:\d\d:\d\d push 134500 ")


class Stall(RuntimeError):
    pass
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
    raise Stall("no %s within %.0f s (last: %r)" % (what, timeout, last))


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
    end = time.time() + LIMIT_READY
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
        raise Stall("FSM did not reach Ready within %d s (last %s)" % (LIMIT_READY, last))
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


def top_checks():
    """Top-camera results the mock has sent (one per tape check)."""
    try:
        with open(os.path.join(LOGS, "vision_mock.log"), encoding="utf-8", errors="replace") as f:
            # "HH:MM:SS push 134500 ..." -- not "MUTED push" (fault injection)
            return sum(1 for l in f if TOP_PUSH.match(l))
    except OSError:
        return 0


def diagnose(why):
    log("STALL:", why)
    print("  UI, last checkpoints:")
    for l in [l for l in tail("ui", 400) if "checkpoint" in l or "error" in l.lower()][-8:]:
        print("   ", l[:200])
    print("  vision mock, last lines:")
    for l in tail("vision_mock", 6):
        print("   ", l[:200])
    try:
        with urllib.request.urlopen("http://%s:8126/v" % PLC_HOST[0], timeout=2) as r:
            print("  PLC /v:", r.read().decode()[:160])
    except Exception as e:
        print("  PLC /v: NOT ANSWERING (%s)" % e)


PLC_HOST = ["192.168.1.70"]


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
    ap.add_argument("--parts", type=int, default=None,
                    help="plan exactly this many good parts and run until the plan is done (overrides --cycles)")
    ap.add_argument("--ng-side", type=float, default=None, help="vision mock NG rate, side camera")
    ap.add_argument("--ng-btm", type=float, default=None, help="vision mock NG rate, bottom camera")
    ap.add_argument("--ng-tape", type=float, default=None, help="vision mock NG rate, parts in the tape")
    ap.add_argument("--seed", type=int, default=None, help="vision mock random seed")
    ap.add_argument("--stop-after", type=int, default=None,
                    help="press STOP after this many tape checks and report how the loop stops")
    ap.add_argument("--save-as", default=None,
                    help="copy this run's logs to sim_logs/runs/<name>/ (for gantt.py --run)")
    ap.add_argument("--vision-first", action="store_true", help="connect vision before the PLC")
    ap.add_argument("--no-ui-start", action="store_true", help="UI already running with XPLC_HARNESS=1")
    a = ap.parse_args()

    PLC_HOST[0] = a.plc
    collector = None
    procs = []
    stalled = False
    try:
        plc_prepare()
        procs.append(start("remote_harness", [sys.executable, os.path.join(SCRIPTS, "internals", "remote_harness.py")]))
        events_csv = os.path.join(LOGS, "events.csv")
        for stale in (events_csv, events_csv + ".start"):
            if os.path.exists(stale):
                os.remove(stale)
        mock = [sys.executable, os.path.join(REPO, "tools", "sim", "vision_mock.py"), "--plc", "%s:8126" % a.plc,
                "--events-out", events_csv]
        for opt in ("ng_side", "ng_btm", "ng_tape", "seed"):
            if getattr(a, opt) is not None:
                mock += ["--" + opt.replace("_", "-"), str(getattr(a, opt))]
        procs.append(start("vision_mock", mock))
        if not a.no_ui_start:
            env = os.environ.copy()
            env["PATH"] = NODE_DIR + os.pathsep + env.get("PATH", "")
            env["XPLC_HARNESS"] = "1"
            env["XPLC_CONSOLE"] = "1"
            procs.append(start("ui", [os.path.join(NODE_DIR, "node.exe"), os.path.join(REPO, "standalone", "run.cjs")], env))

        wait_for("UI answering the harness", lambda: push("ping", timeout=5), timeout=LIMIT_UI_UP)
        log("UI up:", push("get_state"))

        def link_plc():
            push("connect_tcp", {"host": a.plc, "port": 8125})
            wait_for("PLC link", lambda: push("get_state")["tcpConnected"], timeout=LIMIT_LINK)

        def link_vision():
            push("connect_vision", {"host": "localhost", "port": 7950})
            wait_for("vision link", lambda: push("get_state")["visionStatus"] == 2, timeout=LIMIT_LINK)

        # Either order must work (review 2026-09-24 R-P0-2); --vision-first
        # exercises the one that used to drop every vision reply.
        for step in ((link_vision, link_plc) if a.vision_first else (link_plc, link_vision)):
            step()
        push("connect_feeder", {"port": "MOCK"})
        wait_for("feeder bridge", lambda: push("get_state")["feederStatus"] == 2, timeout=LIMIT_LINK)
        log("links up:", push("get_state"))

        bring_to_ready(a.home)
        push("set_tab", {"tab": "Calib"}, timeout=10)
        plan = [a.parts] if a.parts else [a.cycles + 5]
        if a.parts:
            a.cycles = 10 ** 6            # run until the plan completes
        log("plan:", push("set_plan", {"plan": plan}, timeout=10))
        open(events_csv + ".start", "w").close()      # vision_mock starts collecting
        collector = events_csv
        log("run_cycle:", push("run_cycle", timeout=10))

        base = top_checks()                 # the mock log is per run, but be safe
        t_run = time.time()
        last_count, t_last = 0, None
        while True:
            time.sleep(1.0)
            rs = push("get_running_state", timeout=10)
            checks = top_checks() - base
            now = time.time()
            if checks != last_count:
                last_count, t_last = checks, now
            log("running=%s state=%r err=%r tape_checks=%d pack=%r" % (
                rs.get("isRunning"), rs.get("runningState"), rs.get("currentError"),
                checks, rs.get("packInfoString")))
            if rs.get("currentError") or not rs.get("isRunning"):
                log("stopped or error; vision mock tail:")
                for l in tail("vision_mock", 8):
                    print("   ", l)
                break
            if checks >= a.cycles:
                break
            if a.stop_after and checks >= a.stop_after:
                log("STOP pressed after %d tape checks" % checks)
                r = push("stop_cycle", timeout=16)
                rs = push("get_running_state", timeout=10)
                log("stop_cycle ->", r, "| running=%s state=%r" % (rs.get("isRunning"), rs.get("runningState")))
                break
            if t_last is None and now - t_run > LIMIT_FIRST_CHECK:
                raise Stall("no tape check within %d s of RUN" % LIMIT_FIRST_CHECK)
            if t_last is not None and now - t_last > LIMIT_BETWEEN_CHECKS:
                raise Stall("no new tape check for %d s (after %d)" % (LIMIT_BETWEEN_CHECKS, checks))
        push("stop_cycle", timeout=15)
        log("stop_cycle sent")
    except Stall as e:
        stalled = True
        diagnose(str(e))
        try:
            push("stop_cycle", timeout=5)
        except Exception:
            pass
    finally:
        if collector is not None:
            time.sleep(0.5)                               # last event poll
            try:
                events = event_log.load(collector)
                log("event log: %d events -> %s" % (len(events), collector))
                event_log.analyze(events)
            except (OSError, ValueError) as e:
                log("event log unreadable:", e)
        if a.save_as:
            import shutil
            dst = os.path.join(LOGS, "runs", a.save_as)
            os.makedirs(dst, exist_ok=True)
            for f in ("events.csv", "ui.log", "vision_mock.log"):
                if os.path.exists(os.path.join(LOGS, f)):
                    shutil.copy(os.path.join(LOGS, f), dst)
            log("run saved to", dst)
        for p in reversed(procs):
            try:
                p.terminate()
            except Exception:
                pass
        log("stopped helpers; logs in", LOGS)
    return 2 if stalled else 0


if __name__ == "__main__":
    sys.exit(main())
