"""Write new PLC code with the machine made safe first (owner's sequence,
2026-10-05): no motion -> Error state -> stop + reset -> import/build ->
download -> wait -> start -> check. Replaces safe_install for code changes
made on disk.

    python tools/deploy.py --owner-ok [--reset warm|cold] [--max-download 45]
                           [--settle 5] [--skip-reset] [--dry-run]

Edit the .st files on disk, then run this. Do NOT import them first
(`rpc.py push`): the reset step logs in with Keep, which plc_guard allows
only while the project still matches what the PLC runs. Once the project
differs, any login transfers code; --skip-reset then downloads without the
separate reset (the old safe_install behaviour, with the other checks).

Steps, each timed, logged to codesys_scripts/jobs/deploy_logs/:
  1. daemon fresh enough (restart it now, not mid-deploy, near its 4 h
     budget) and one login to read the bus state: EtherCAT must be up;
  2. no motion: no stream running, motion buffer 0, delta axes powered off
     and the set positions not changing; the PLC log saved;
  3. FSM to Error (GA_EV 9: group disabled, reel off), delta axes off;
  4. the UI's PLC link closed, so nothing talks to the PLC while it changes;
  5. stop + reset (jobs/templates/stop_and_reset.py);
  6. import + build (`rpc.py push --no-online`), 0 errors required;
  7. download, wait --settle s, start, watch xConfigFinished
     (jobs/templates/download_wait_start.py); a download slower than
     --max-download s is not started and the tool stops;
  8. the UI relinked: FSM UnInited, delta virtual, EtherCAT lost 0; the
     layout baseline saved.

Stops at the first failed step and says what state the machine is in.
One attempt only: it never downloads twice (ask the owner first).
"""

import argparse
import os
import re
import sys
import time

import machine as mc

REPO = mc.REPO
TEMPLATES = os.path.join(REPO, "codesys_scripts", "jobs", "templates")
LOGDIR = os.path.join(REPO, "codesys_scripts", "jobs", "deploy_logs")
BUDGET_S = 14400        # codesys_env.json session_max_seconds
FRESH_MARGIN_S = 1800   # restart the daemon first when less than this is left

_logf = None


def say(*a):
    msg = time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a)
    print(msg, flush=True)
    if _logf:
        _logf.write(msg + "\n")
        _logf.flush()


class Step:
    def __init__(self, name):
        self.name = name

    def __enter__(self):
        self.t0 = time.time()
        say("--", self.name)
        return self

    def __exit__(self, et, ev, tb):
        dt = time.time() - self.t0
        say("   %s %.1fs" % ("ok" if et is None else "FAILED", dt))
        return False


def fail(why, state):
    say("STOPPED:", why)
    say("machine state:", state)
    raise SystemExit(2)


def job(template, label, plc, params=None, timeout=600):
    """Run a job template through the daemon (machine.run_job), log its output."""
    lines = mc.run_job(template, label, plc, params, timeout=timeout)
    for l in lines:
        say("   |", l)
    return lines


def daemon_uptime():
    lines = mc.rpc("ping", timeout=60)
    for l in lines:
        m = re.match(r"\s*uptime\s+([\d.]+)", l)
        if m:
            return float(m.group(1))
    return None


def main():
    global _logf
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--owner-ok", action="store_true")
    ap.add_argument("--reset", choices=("warm", "cold"), default="warm")
    ap.add_argument("--max-download", type=float, default=45.0)
    ap.add_argument("--settle", type=float, default=5.0)
    ap.add_argument("--bus-wait", type=float, default=30.0)
    ap.add_argument("--skip-reset", action="store_true",
                    help="the project already differs from the PLC: download without the separate reset")
    ap.add_argument("--dry-run", action="store_true", help="steps 1-2 only: check, change nothing")
    ap.add_argument("--allow-bus-down", action="store_true",
                    help="local sim only: its EtherCAT master cannot open (no Npcap)")
    ap.add_argument("--post-import", metavar="JOB",
                    help="local sim only: a job file run after the import (e.g. the sim's library patch)")
    a = ap.parse_args()
    mc.require_owner_ok(a.owner_ok)
    os.makedirs(LOGDIR, exist_ok=True)
    _logf = open(os.path.join(LOGDIR, time.strftime("deploy_%Y%m%d-%H%M%S.log")), "w", encoding="utf-8")
    say("deploy: reset %s, max download %.0f s, settle %.0f s%s" % (
        a.reset, a.max_download, a.settle, " (DRY RUN)" if a.dry_run else ""))

    with Step("1. daemon and bus"):
        up = daemon_uptime()
        say("   daemon uptime", up, "s")
        if up is None or up > BUDGET_S - FRESH_MARGIN_S:
            say("   restarting the daemon now, before anything touches the PLC")
            mc.restart_daemon()
        ok, ec = mc.bus_up()       # one login (Keep) through the daemon
        say("   EtherCAT", ec)
        if "project differs" in str(ec) and not a.skip_reset:
            # plc_guard: the project is not what the PLC runs (already
            # imported, or the fingerprint moved with a daemon restart).
            # Then no login can stop/reset without transferring code.
            fail("the project already differs from what the PLC runs, so the reset step cannot log in. "
                 "If you KNOW the PLC runs this project: `rpc.py mark-synced --i-know`; "
                 "else --skip-reset", "unchanged")
        if not ok and not a.allow_bus_down:
            fail("EtherCAT is not up before the deploy: fix the bus first", "unchanged")

    with Step("2. no motion"):
        mc.reconnect()
        ss = mc.push("plc_stream_status", {})
        if ss.get("running"):
            fail("a motion stream is running", "unchanged, moving")
        st1 = mc.sys_cmd("GET_MACHINE_STATE")
        p1 = [mc.sys_cmd("AXIS_INFO", axis=k) for k in range(3)]
        time.sleep(1.0)
        st2 = mc.sys_cmd("GET_MACHINE_STATE")
        p2 = [mc.sys_cmd("AXIS_INFO", axis=k) for k in range(3)]
        say("   FSM %s, motion buffer %s/%s, delta states %s" % (
            st2.get("st_str"), st1.get("motion_buffer_size"), st2.get("motion_buffer_size"), [p["st"] for p in p2]))
        if st1.get("motion_buffer_size") or st2.get("motion_buffer_size"):
            fail("motion is queued", "unchanged")
        moving = [k for k in range(3) if any(
            isinstance(p1[k].get(f), (int, float)) and abs(p1[k][f] - p2[k].get(f, p1[k][f])) > 1e-6
            for f in ("pos", "act", "vel"))]
        if moving:
            fail("delta axes %s still moving" % moving, "unchanged")
        # A download wipes RETAIN: an open tape move would be forgotten and
        # the tape left between cells (plan audit 2026-10-06, #2). Finish it
        # first (RUN does, through REEL_RESUME).
        try:
            plan = mc.sys_cmd("PLAN_GET")
        except Exception as e:
            plan = {}
            say("   PLAN_GET failed: %s" % e)
        say("   plan: id %s, cells %s of %s, reel_open %s" % (
            plan.get("plan_id"), plan.get("cells_done"), sum(abs(n) for n in plan.get("seg", []) or []),
            plan.get("reel_open")))
        if plan.get("reel_open"):
            fail("a tape move is open (PLAN_GET reel_open): finish it with RUN first, a download would lose it",
                 "unchanged")
        if plan.get("seg") and plan.get("cells_done", 0) < sum(abs(n) for n in plan["seg"]):
            say("   NOTE: the PLC holds an unfinished plan; the download wipes it, and the next RUN "
                "asks the operator before pushing the UI's count back")
        say("   PLC log saved:", mc.save_plc_log())
        if a.dry_run:
            say("dry run: checks passed, nothing changed")
            return

    with Step("3. FSM to Error"):
        mc.sys_cmd("GA_EV", ev=9)       # EV_ERROR: group disabled, reel off
        for _ in range(40):
            st, _r = mc.fsm()
            if st == "Error":
                break
            time.sleep(0.25)
        else:
            fail("FSM did not enter Error (%s)" % st, "FSM %s" % st)
        for _ in range(40):
            axes = mc.axis_states()
            if axes == [0, 0, 0]:
                break
            time.sleep(0.25)
        else:
            fail("delta axes not powered off in Error: %s" % axes, "FSM Error")
        say("   FSM Error, delta axes", axes)

    with Step("4. UI link closed"):
        mc.push("disconnect_tcp", timeout=20)
        time.sleep(2.0)

    if a.skip_reset:
        say("-- 5. stop + reset: SKIPPED (--skip-reset)")
    else:
        with Step("5. stop + reset %s" % a.reset):
            lines = job("stop_and_reset.py", "deploy_reset", "keep", {"RESET": a.reset})
            if not any("reset %s done" % a.reset in l for l in lines):
                fail("stop/reset did not complete (if the guard refused: the project already "
                     "differs from the PLC, use --skip-reset)", "FSM Error, UI link closed, app state unknown")

    with Step("6. import + build"):
        lines = mc.rpc("push", "--no-online", timeout=600)
        build = [l for l in lines if l.startswith("BUILD:") or "summary:" in l]
        for l in build:
            say("   |", l)
        if a.post_import:
            lines = mc.rpc("exec", "--label", "deploy_post_import", "--file", os.path.abspath(a.post_import), timeout=600)
            for l in lines[-3:]:
                say("   |", l)
            lines = [l.replace("build errors: 0", "BUILD: errors=0") for l in lines]
        if not any(re.search(r"BUILD: errors=0\b", l) for l in lines):
            for l in [l for l in lines if "[ERR]" in l][:10]:
                say("   |", l)
            fail("build errors", "app stopped and reset (old code), UI link closed")

    with Step("7. download, wait, start"):
        lines = job("download_wait_start.py", "deploy_download", "download",
                    {"MAX_DOWNLOAD_S": a.max_download, "SETTLE_S": a.settle, "BUS_WAIT_S": a.bus_wait},
                    timeout=900)
        if any("DOWNLOAD SLOW" in l for l in lines):
            fail("download slower than %.0f s: not started. Ask the owner before anything else."
                 % a.max_download, "new code downloaded, app STOPPED")
        if not any("post-start state:" in l and "run" in l.lower() for l in lines):
            fail("application did not start", "new code downloaded, app not running")
        if not any("ethercat: UP" in l for l in lines) and not a.allow_bus_down:
            fail("EtherCAT did not come up after the start. Save the PLC log; do NOT download again "
                 "without the owner.", "new code running, bus down")

    with Step("8. relink and check"):
        mc.push("connect_tcp", timeout=20)
        time.sleep(2.0)
        mc.reconnect()
        st, _r = mc.fsm()
        ec = mc.sys_cmd("EC_STATS")
        say("   FSM %s, delta mask %s, axes %s, EtherCAT lost %s" % (
            st, mc.delta_mask(), mc.axis_states(), ec.get("lost")))
        sys.path.insert(0, os.path.join(REPO, "codesys_scripts"))
        import layout_check
        say("   layout baseline:", layout_check.save_baseline(layout_check.scan_disk(), "deploy"))
    say("deploy done")


if __name__ == "__main__":
    main()
