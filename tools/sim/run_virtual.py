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
        if r.get("ok") or "not logged in" not in str(r.get("error", "")).lower():
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


def path_test_matrix(a):
    """Feed a circle of a.path_n G1 moves under every combination and log
    how long the arm took against the time its moves need."""
    rows = []
    for per_scan in (1, 4):
        daemon({"cmd": "write", "symbol": "GVL.CommPacketsPerScan", "value": str(per_scan)})
        daemon({"cmd": "logout"})
        for nodelay in (False, True):
            push("tcp_nodelay", {"on": nodelay}, timeout=10)
            for feed in (200, 1000):
                for mode in ("await", "queue"):
                    r = push("path_test", {"n": a.path_n, "radius": 20, "feed": feed, "mode": mode},
                             timeout=120)
                    ideal = r["pathMm"] / feed * 1000.0
                    row = (per_scan, nodelay, feed, mode, r["ms"], r["sendMs"], ideal)
                    rows.append(row)
                    log("path: pkts/scan=%d nodelay=%-5s feed=%4d %-5s  %5d ms (send %5d ms)  "
                        "moves alone %4.0f ms  x%.2f" % (row + (r["ms"] / ideal,)))
    daemon({"cmd": "write", "symbol": "GVL.CommPacketsPerScan", "value": "4"})
    daemon({"cmd": "logout"})
    push("tcp_nodelay", {"on": True}, timeout=10)
    return rows


# Peak monitor axes (GVL.MotionPeak* index) and joint -> motor gearing.
PEAK_AXES = [("EAxis0", 31), ("EAxis1", 31), ("EAxis2", 31),
             ("SM_Drive_GenericDSP402 (group A)", 1), ("EAXIS_A", 1), ("reelpullmotor", 1)]


def read_peaks():
    out = []
    for i, (name, ratio) in enumerate(PEAK_AXES):
        v = [float(str(daemon({"cmd": "read", "symbol": "GVL.MotionPeak%s[%d]" % (k, i)})["value"]).split("#")[-1])
             for k in ("Vel", "Acc", "Jerk")]
        out.append((name, ratio, v))
    daemon({"cmd": "logout"})
    return out


def report_peaks():
    """Joint values as the PLC plans them, and the motor side (x gearing):
    motor speed in rpm, acceleration in rev/s^2, jerk in rev/s^3."""
    log("servo peaks (set values, whole run):")
    for name, ratio, (vel, acc, jerk) in read_peaks():
        line = "  %-34s |v| %9.1f  |a| %11.1f  |j| %13.1f  (axis units/s^n)" % (name, vel, acc, jerk)
        if ratio != 1:
            line += "   motor: %6.0f rpm  %7.1f rev/s2  %9.1f rev/s3" % (
                vel * ratio / 6.0, acc * ratio / 360.0, jerk * ratio / 360.0)
        log(line)


def _w(sym, val):
    daemon({"cmd": "write", "symbol": "GVL." + sym, "value": val})


def _r(sym):
    return str(daemon({"cmd": "read", "symbol": "GVL." + sym})["value"]).split("#")[-1]


def override_probe():
    """MC_GroupSetOverride against the same moves: which factors act, on
    running or only on new moves, and does it report an error."""
    import threading

    def setf(enable, vel=1.0, acc=1.0, jerk=1.0):
        _w("TestOvrVel", repr(vel)); _w("TestOvrAcc", repr(acc)); _w("TestOvrJerk", repr(jerk))
        _w("TestOvrEnable", "TRUE" if enable else "FALSE")
        time.sleep(0.3)
        st = "en=%s busy=%s err=%s id=%s" % (_r("TestOvrEnabled"), _r("TestOvrBusy"), _r("TestOvrError"), _r("TestOvrErrorId"))
        daemon({"cmd": "logout"})
        return st

    def run(label, st, feed, mid=None):
        for shape, n, cor in (("square", 4, 0.5), ("24-gon", 24, 3)):
            _w("MotionPeakReset", "TRUE"); daemon({"cmd": "logout"})
            t = None
            if mid:
                def later():
                    time.sleep(mid[0])
                    for k, v in mid[1].items():
                        _w(k, v)
                    daemon({"cmd": "logout"})
                t = threading.Thread(target=later); t.start()
            r = push("path_test", {"n": n, "radius": 30, "feed": feed, "mode": "queue", "cor": cor}, timeout=180)
            if t:
                t.join()
            pk = read_peaks()
            v = max(p[2][0] for p in pk[:3]); acc = max(p[2][1] for p in pk[:3])
            log("probe: %-34s F%4d %-6s %5d ms  |v| %5.0f  |a| %6.0f   [%s]" % (label, feed, shape, r["ms"], v, acc, st))

    push("set_speed", {"percent": 100}, timeout=10)
    for feed in (200, 1000):
        run("off", setf(False), feed)
        run("vel 0.3", setf(True, vel=0.3), feed)
        run("acc 0.09", setf(True, acc=0.09), feed)
        run("jerk 0.027", setf(True, jerk=0.027), feed)
        run("vel .3 acc .09 jerk .027", setf(True, 0.3, 0.09, 0.027), feed)
        run("vel 0.3 again (after the above)", setf(True, vel=0.3), feed)
        st = setf(False, vel=0.3)
        _w("TestOvrEnable", "TRUE"); daemon({"cmd": "logout"}); time.sleep(0.3)
        run("vel 0.3, Enable re-triggered", st + " -> re-enabled en=" + _r("TestOvrEnabled"), feed)
        daemon({"cmd": "logout"})
        run("vel 1 -> 0.3 mid-move (after 0.3 s)", setf(True, vel=1.0), feed,
            mid=(0.3, {"TestOvrVel": "0.3"}))
    setf(False)


def override_check():
    """Same square (4 stop-to-stop moves of ~42 mm, and a blended 24-gon)
    at 100 % and 30 %: how do the peaks scale?"""
    for label, n, cor in (("square, stop at corners", 4, 0.5), ("24-gon, blended", 24, 3)):
        base = None
        for pct in (100, 30):
            push("set_speed", {"percent": pct}, timeout=10)
            daemon({"cmd": "write", "symbol": "GVL.MotionPeakReset", "value": "TRUE"})
            daemon({"cmd": "logout"})
            r = push("path_test", {"n": n, "radius": 30, "feed": 1000, "mode": "queue", "cor": cor}, timeout=120)
            pk = read_peaks()
            v = max(p[2][0] for p in pk[:3]); acc = max(p[2][1] for p in pk[:3]); j = max(p[2][2] for p in pk[:3])
            if base is None:
                base = (r["ms"], v, acc, j)
            log("override: %-24s %3d %%  %5d ms (x%.2f)  |v| %6.0f (x%.2f)  |a| %7.0f (x%.3f)  |j| %9.0f (x%.4f)"
                % (label, pct, r["ms"], r["ms"] / base[0], v, v / base[1], acc, acc / base[2], j, j / base[3]))
    push("set_speed", {"percent": 100}, timeout=10)


def abort_test_matrix(a):
    """Peaks and redirect time for each way of turning toward the bin."""
    cases = [("blend", 1.0), ("abort", 1.0), ("stepped", 4), ("stepped", 6)]
    for delay in (30, 80):
        for mode, scale in cases:
            daemon({"cmd": "write", "symbol": "GVL.MotionPeakReset", "value": "TRUE"})
            daemon({"cmd": "logout"})
            r = push("abort_test", {"mode": mode, "delayMs": delay, "scale": scale if mode != "stepped" else 1,
                                   "steps": int(scale) if mode == "stepped" else None, "feed": 1000}, timeout=60)
            peaks = read_peaks()
            acc = max(p[2][1] for p in peaks[:3])
            jerk = max(p[2][2] for p in peaks[:3])
            vel = max(p[2][0] for p in peaks[:3])
            log("redirect: after %3d ms  %-7s x%.2f  redirect %4d ms  total %4d ms   delta joints max "
                "|v| %6.0f  |a| %7.0f  |j| %9.0f  (motor %5.0f rpm %6.0f rev/s2)"
                % (delay, mode, scale, r["redirectMs"], r["totalMs"], vel, acc, jerk,
                   vel * 31 / 6.0, acc * 31 / 360.0))


def path_jerk_matrix(a):
    """Blended circle (Cor 3): does the per-segment floor follow the jerk?
    feedConfig uses JERK = F*400 with ACC = F*100, i.e. 0.25 s to reach
    full acceleration, so short moves never do."""
    for n in (25, 50, 200):
        for feed in (200, 1000):
            for jr in (400, 2000, 10000):
                r = push("path_test", {"n": n, "radius": 20, "feed": feed, "mode": "queue", "cor": 3,
                                       "jerkRatio": jr}, timeout=120)
                ideal = r["pathMm"] / feed * 1000.0
                log("jerk: n=%3d seg=%5.2f mm feed=%4d JERK=F*%-5d %5d ms  %5.1f ms/seg  moves alone %4.0f ms  x%.2f"
                    % (n, r["segmentMm"], feed, jr, r["ms"], r["ms"] / n, ideal, r["ms"] / ideal))
    push("path_test", {"n": 4, "radius": 1, "feed": 200, "mode": "queue", "cor": 45, "jerkRatio": 400}, timeout=60)


def path_motion_matrix(a):
    """Same circle, transport fixed (queue, 4 packets/scan, NoDelay): vary
    the number of segments and the corner blending to see whether the time
    follows the path length (moves blend) or the segment count (the arm
    stops at every point)."""
    def retries():
        v = daemon({"cmd": "read", "symbol": "GVL.G1RetryCount"})["value"]
        return int(str(v).split("#")[-1])
    for n in (25, 50, 200):
        for cor in (0.5, 3):
            for feed in (200, 1000):
                before = retries()
                r = push("path_test", {"n": n, "radius": 20, "feed": feed, "mode": "queue", "cor": cor},
                         timeout=120)
                ideal = r["pathMm"] / feed * 1000.0
                log("motion: n=%3d seg=%5.2f mm Cor=%4.1f feed=%4d  %5d ms  %5.1f ms/seg  "
                    "moves alone %4.0f ms  x%.2f  G1 retries %d" % (n, r["segmentMm"], cor, feed, r["ms"],
                                                    r["ms"] / n, ideal, r["ms"] / ideal, retries() - before))
    push("path_test", {"n": 4, "radius": 1, "feed": 200, "mode": "queue", "cor": 45}, timeout=60)


def plc_prepare():
    # Refuse unless the delta arms are virtual: the virtual scene must never
    # move them. EAXIS_A (the A rotation) and the reel are cleared to move.
    for ax in ("EAxis0", "EAxis1", "EAxis2"):
        v = daemon({"cmd": "read", "symbol": "IoConfig_Globals.%s.bVirtual" % ax})["value"]
        if v.upper() != "TRUE":
            raise SystemExit("ABORT: %s is not virtual (%s)" % (ax, v))
    daemon({"cmd": "write", "symbol": "GVL.SimDigitalInputEnable", "value": "TRUE"})
    if PLC_HOST[0] in ("127.0.0.1", "localhost"):
        # PC soft-PLC sim: no EtherCAT adapter, so the master always errors.
        # The PLC honours this only while every axis is virtual (GVL.st).
        daemon({"cmd": "write", "symbol": "GVL.SimNoFieldbus", "value": "TRUE"})
        log("PLC: local sim, EtherCAT master error ignored (all axes virtual)")
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


def chaos(a):
    """STOP at random moments (a.chaos times), RUN again after each, then let
    the plan finish. Each STOP waits for the loop to really end. Seeded, so
    a failing sequence can be replayed with the same --chaos-seed.
    With --chaos-in-empty, STOP only while the plan is in an empty-cell
    segment (plan[0] < 0), a random 0-1 s after noticing it."""
    import random
    rng = random.Random(a.chaos_seed)
    stops = 0
    t_run = time.time()
    wait = rng.uniform(2.0, 6.0)
    end = time.time() + 600
    t_gap = None
    while time.time() < end:
        time.sleep(0.25 if not a.chaos_in_empty else 0.05)
        rs = push("get_running_state", timeout=10)
        if rs.get("currentError") or "errorString" in str(rs.get("runningState")):
            log("chaos: error", rs.get("currentError") or rs.get("runningState"))
            return
        if not rs.get("isRunning"):
            left = push("get_plan", timeout=10).get("plan")
            if not left:
                log("chaos: plan done after %d stops" % stops)
                return
        if a.chaos_in_empty and stops < a.chaos and rs.get("isRunning"):
            plan = push("get_plan", timeout=10).get("plan") or []
            if plan and plan[0] < 0:
                if t_gap is None:
                    t_gap, wait = time.time(), rng.uniform(0.0, 1.0)
                    t_run = t_gap - 1e9          # the gap timer decides
                    wait += 1e9
            else:
                t_gap = None
                continue
        if stops < a.chaos and rs.get("isRunning") and time.time() - t_run > wait:
            r = push("stop_cycle", timeout=16)
            left = push("get_plan", timeout=10).get("plan")
            stops += 1
            log("chaos: STOP #%d -> %s, plan left %s" % (stops, r, left))
            time.sleep(rng.uniform(0.3, 2.0))
            if left and a.forget_at_stop:
                # What a renderer crash/restart loses: its plan. RUN must
                # then resume from the plan the PLC keeps.
                plc = push("get_plc_plan", timeout=10)
                push("forget_plan", timeout=10)
                log("chaos: renderer plan forgotten; PLC has %s at cell %s -> %s" % (
                    plc.get("seg"), plc.get("cells_done"), plc.get("remaining")))
            if left:
                push("run_cycle", timeout=10)
                log("chaos: RUN again")
            t_run = time.time()
            wait = rng.uniform(2.0, 6.0)
            t_gap = None
    log("chaos: gave up after 10 min")


REEL_CELL_MM = 8.0          # TAPE.REEL_CELL_DISTANCE (lib/production/params.ts)
REEL_PERIOD_MM = 200.0      # the reel is a modulo axis (fPositionPeriod)


def reel_pos():
    v = float(_r_sym("AxisGroupSM.reelpullmotor_fActPosition"))
    daemon({"cmd": "logout"})
    return round(v, 3)


def _r_sym(sym):
    return str(daemon({"cmd": "read", "symbol": sym})["value"]).split("#")[-1]


def reel_books(pos0, cells0):
    """The tape moved exactly as far as the PLC counted?"""
    pos1 = reel_pos()
    plc = push("get_plc_plan", timeout=10)
    # the position wraps every REEL_PERIOD_MM: compare modulo one turn
    counted = (plc.get("cells_done") or 0) - cells0
    off = (pos1 - pos0 - counted * REEL_CELL_MM) % REEL_PERIOD_MM
    off = min(off, REEL_PERIOD_MM - off)
    ok = off < 0.05
    log("reel books: PLC counted %d cells, reel %.3f mm off that (mod %g) -> %s" % (
        counted, off, REEL_PERIOD_MM, "MATCH" if ok else "MISMATCH"))
    return ok


def fault(a):
    """Drive the PLC into Error at random moments (a.fault times) -- what a
    servo trip, a bus drop or an E-stop looks like to the software -- then
    recover the way an operator would: reset + power + enable + ready
    (bring_to_ready), then RUN. Logs what the UI did at each fault (loop
    ended / held on an error / kept going) and the plan both sides hold."""
    import random
    rng = random.Random(a.chaos_seed)
    faults = 0
    t_run = time.time()
    wait = rng.uniform(2.0, 6.0)
    end = time.time() + 900
    while time.time() < end:
        time.sleep(0.25)
        rs = push("get_running_state", timeout=10)
        if not rs.get("isRunning"):
            left = push("get_plan", timeout=10).get("plan")
            if not left:
                log("fault: plan done after %d faults" % faults)
                return
            if faults >= a.fault:
                log("fault: loop ended with plan left %s: %s" % (left, rs.get("runningState")))
                return
        if faults < a.fault and rs.get("isRunning") and time.time() - t_run > wait:
            plan_before = push("get_plan", timeout=10).get("plan")
            if a.fault_mid_reel:
                # the PLC trips itself once the next tape move is running
                _w("TestFaultMidReel", "2" if a.estop else "1"); daemon({"cmd": "logout"})
                wait_for("mid-reel fault", lambda: push("ga_ev", {"ev": EV_NONE}, timeout=10)["reply"].get("st_str") == "Error",
                         timeout=60, period=0.2)
            else:
                push("enter_error", timeout=10)
            faults += 1
            log("fault: #%d injected, plan %s, reel at %s" % (faults, plan_before, reel_pos()))
            # What does the UI do? Give it a few seconds.
            t0 = time.time()
            while time.time() - t0 < 8:
                time.sleep(0.25)
                rs = push("get_running_state", timeout=10)
                if not rs.get("isRunning") or rs.get("currentError"):
                    break
            log("fault: after %.1f s running=%s err=%r state=%r" % (
                time.time() - t0, rs.get("isRunning"), rs.get("currentError"), rs.get("runningState")))
            if rs.get("isRunning"):
                r = push("stop_cycle", {"wait_ms": 8000}, timeout=16)
                rs = push("get_running_state", timeout=10)
                log("fault: stop_cycle -> %s, running=%s" % (r, rs.get("isRunning")))
                if rs.get("isRunning"):
                    # held on the error: release the hold, the stop flag ends the loop
                    push("resume_cycle", timeout=16)
                    wait_for("loop end", lambda: not push("get_running_state", timeout=10).get("isRunning"),
                             timeout=30)
            plc = push("get_plc_plan", timeout=10)
            log("fault: renderer plan %s | PLC %s at cell %s -> %s" % (
                push("get_plan", timeout=10).get("plan"), plc.get("seg"), plc.get("cells_done"),
                plc.get("remaining")))
            time.sleep(rng.uniform(0.5, 2.0))
            log("fault: reel settled at %s" % reel_pos())
            bring_to_ready(a.home)
            left = push("get_plan", timeout=10).get("plan")
            if left:
                push("run_cycle", timeout=10)
                log("fault: RUN again")
            t_run = time.time()
            wait = rng.uniform(2.0, 6.0)
    log("fault: gave up after 15 min")


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
    ap.add_argument("--plan", default=None,
                    help='production plan, e.g. "3,-2,4" (pack 3, leave 2 empty, pack 4); overrides --parts')
    ap.add_argument("--chaos", type=int, default=0,
                    help="press STOP at a random moment this many times, RUN again each time, then let the plan finish")
    ap.add_argument("--chaos-seed", type=int, default=1)
    ap.add_argument("--fault-mid-reel", action="store_true",
                    help="with --fault: trip while a tape move is running (GVL.TestFaultMidReel)")
    ap.add_argument("--estop", action="store_true",
                    help="with --fault-mid-reel: an E-stop, the reel stops where it is too")
    ap.add_argument("--fault", type=int, default=0,
                    help="drive the PLC into Error N times mid-run, recover (reset/power/ready) and RUN again")
    ap.add_argument("--path-test", action="store_true",
                    help="instead of production: stream short G1 moves (path_test) under each "
                         "combination of PLC packets/scan, TCP NoDelay and await/queue, and report")
    ap.add_argument("--path-n", type=int, default=200)
    ap.add_argument("--speed", type=int, default=100, help="motion speed override in percent (set_speed)")
    ap.add_argument("--speed-wobble", action="store_true",
                    help="while running, switch the speed between 100/40/70 % every few seconds")
    ap.add_argument("--peaks", action="store_true",
                    help="report each servo's peak |velocity|, |acceleration| and |jerk| over the run "
                         "(GVL.MotionPeak*, reset at RUN)")
    ap.add_argument("--override-probe", action="store_true",
                    help="instead of production: probe how MC_GroupSetOverride behaves (GVL.TestOvr*)")
    ap.add_argument("--override-check", action="store_true",
                    help="instead of production: the same moves at 100 % and 30 % speed, servo peaks of each")
    ap.add_argument("--abort-test", action="store_true",
                    help="instead of production: redirect toward the NG bin mid-move, abort (dynamics "
                         "scaled) vs blend, with the servo peaks of each")
    ap.add_argument("--path-jerk", action="store_true",
                    help="with --path-test: vary the jerk ratio (JERK = F * ratio)")
    ap.add_argument("--path-motion", action="store_true",
                    help="with --path-test: vary the segment count and corner blending (Cor) "
                         "instead of the transport settings")
    ap.add_argument("--forget-at-stop", action="store_true",
                    help="after each chaos STOP, drop the renderer's plan so RUN resumes from the PLC's")
    ap.add_argument("--chaos-in-empty", action="store_true",
                    help="with --chaos: press STOP only inside an empty-cell segment of the plan")
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
        if a.override_probe:
            override_probe()
            return
        if a.override_check:
            override_check()
            return
        if a.abort_test:
            abort_test_matrix(a)
            return
        if a.path_test:
            if a.path_jerk:
                path_jerk_matrix(a)
            elif a.path_motion:
                path_motion_matrix(a)
            else:
                path_test_matrix(a)
            return
        if a.plan:
            plan = [int(x) for x in a.plan.split(",")]
        else:
            plan = [a.parts] if a.parts else [a.cycles + 5]
        if a.parts or a.plan:
            a.cycles = 10 ** 6            # run until the plan completes
        log("plan:", push("set_plan", {"plan": plan}, timeout=10))
        open(events_csv + ".start", "w").close()      # vision_mock starts collecting
        collector = events_csv
        if a.peaks:
            daemon({"cmd": "write", "symbol": "GVL.MotionPeakReset", "value": "TRUE"})
            daemon({"cmd": "logout"})
        if a.speed != 100:
            log("speed:", push("set_speed", {"percent": a.speed}, timeout=10))
        pos0 = reel_pos() if a.fault else None     # a new plan counts from cell 0
        log("run_cycle:", push("run_cycle", timeout=10))
        if a.speed_wobble:
            import threading
            def wobble():
                k = 0
                while True:
                    time.sleep(4.0)
                    try:
                        if not push("get_running_state", timeout=5).get("isRunning"):
                            break
                        pct = (100, 40, 70)[k % 3]
                        k += 1
                        log("speed wobble:", push("set_speed", {"percent": pct}, timeout=5))
                    except Exception as e:
                        log("speed wobble stopped:", e)
                        break
            threading.Thread(target=wobble, daemon=True).start()
        if a.chaos:
            chaos(a)                        # runs the plan to its end itself
        elif a.fault:
            fault(a)
            reel_books(pos0, 0)
            a.chaos = 1                     # skip the monitor loop below

        base = top_checks()                 # the mock log is per run, but be safe
        t_run = time.time()
        last_count, t_last = 0, None
        while not a.chaos:
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
        if a.peaks:
            report_peaks()
        try:
            plc = push("get_plc_plan", timeout=10)
            log("PLC plan: seg %s cells_done %s -> remaining %s" % (
                plc.get("seg"), plc.get("cells_done"), plc.get("remaining")))
        except Exception as e:                    # older PLC program: no PLAN_GET
            log("PLC plan: unavailable (%s)" % e)
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
