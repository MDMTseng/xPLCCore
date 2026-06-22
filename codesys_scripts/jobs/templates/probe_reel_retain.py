"""A.1 probe -- does the reel axis position survive PLC restart scenarios?

Architecture review (doc_review/architecture_review_2026-06-22.md) §4 (1)
proposes adding the reel axis absolute position to GET_MACHINE_STATE so
the renderer can derive the carrier-tape cell number on resume. That
proposal assumes the position is stable across PLC restart. This probe
checks the assumption directly, without changing PLC code.

Scenarios tested (each in turn):
  1. Stop + Start    -- application restart, no reset. Closest to a
                        renderer-side crash + PLC kept running.
  2. Warm reset      -- clears non-retained vars but keeps retained
                        and persistent. The CODESYS-documented "you
                        can use this between regression runs".
  3. Cold reset      -- clears retained too, keeps persistent.

For each scenario the probe:
  * reads reelpullmotor.fActPosition before the action
  * performs the action via the scripting daemon
  * reads the position again after the action

The probe is read-only against position -- it does NOT command the axis
to move. To make the scenario interesting set the position to a known
non-zero value beforehand (set_prepared_value + force_prepared_values on
the axis's set-position is one way; simpler: jog the reel a few mm
manually, then run this probe).

Caveats:
  * Warm/Cold reset takes the PLC TCP server down. This probe uses the
    daemon (CODESYS scripting host) for reads -- the daemon survives.
  * Position type is REAL via SoftMotion AXIS_REF_SM3.fActPosition.
  * If the drive is a CL3-E57H step motor with no absolute encoder,
    "retained" only means the PLC variable is retained; the drive's
    physical position is lost on drive power-cycle regardless. This
    probe does not differentiate -- if you see fActPosition restored
    after Warm but the actual reel was moved while PLC was down, that's
    PLC-level retain only.

Output: prints a 3-row table (scenario, before, after, delta) and a
verdict per scenario.
"""
import os, sys, subprocess, tempfile, time

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
RPC = [sys.executable, os.path.join(REPO, "codesys_scripts", "rpc.py")]


def daemon_exec(code):
    """Run an IronPython snippet inside the warm CODESYS scripting host."""
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(code); path = f.name
    try:
        return subprocess.check_output(RPC + ["exec", "--file", path],
                                       stderr=subprocess.STDOUT).decode("utf-8", "replace")
    finally:
        os.unlink(path)


# IronPython 2.7 snippet to read reelpullmotor.fActPosition. Result is
# printed so the host script can scrape it.
READ_POS = """
proj = projects.primary
app = next(iter(proj.find("Application", True)))
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)
try:
    v = oapp.read_value("reelpullmotor.fActPosition")
    print("__POS__=" + str(v))
except Exception as e:
    print("__POS__=ERR:" + str(e))
"""


def read_pos():
    """Returns float or None. Strips CODESYS 'REAL#<num>' decoration if present."""
    out = daemon_exec(READ_POS)
    for line in out.splitlines():
        if line.startswith("__POS__="):
            v = line.split("=", 1)[1]
            if v.startswith("ERR:"):
                print("    read failed:", v); return None
            # CODESYS returns stringified IEC values like "REAL#0.0"
            if "#" in v: v = v.split("#", 1)[1]
            try: return float(v)
            except ValueError:
                print("    parse failed:", v); return None
    print("    no __POS__ line found in daemon output:", out[:200])
    return None


# Each action snippet logs in, runs the lifecycle op, restarts.
# Stop + Start: cleanest "PLC kept running, application restarted".
STOP_START = """
proj = projects.primary
app = next(iter(proj.find("Application", True)))
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)
oapp.stop()
import time; time.sleep(0.5)
oapp.start()
"""

WARM_RESET = """
proj = projects.primary
app = next(iter(proj.find("Application", True)))
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)
oapp.reset(ResetOption.Warm)
import time; time.sleep(0.5)
oapp.start()
"""

COLD_RESET = """
proj = projects.primary
app = next(iter(proj.find("Application", True)))
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)
oapp.reset(ResetOption.Cold)
import time; time.sleep(0.5)
oapp.start()
"""


def run_scenario(name, action_code, before, tol=0.001):
    print("--- %s ---" % name)
    print("    before: %s" % before)
    try:
        daemon_exec(action_code)
    except subprocess.CalledProcessError as e:
        print("    action failed:", e.output.decode("utf-8", "replace")[:400])
        return None
    # Give the SoftMotion axis a moment to publish fActPosition after
    # start. fActPosition is updated each scan, but the comm task may
    # need a beat.
    time.sleep(1.5)
    after = read_pos()
    print("    after:  %s" % after)
    if before is None or after is None:
        print("    verdict: INCONCLUSIVE (read failure)")
        return None
    delta = after - before
    if abs(delta) <= tol:
        print("    verdict: RETAINED  (delta=%.4f, within %s)" % (delta, tol))
    else:
        print("    verdict: NOT RETAINED  (delta=%.4f)" % delta)
    return after


def main():
    print("=== probe_reel_retain ===")
    print("Reading initial position...")
    p0 = read_pos()
    print("initial: %s" % p0)
    if p0 is None:
        print("\nFAILED to read initial position -- aborting. Check that")
        print("(a) the daemon is up, (b) the application is logged in /")
        print("running, (c) reelpullmotor exists in the active app.")
        return 1
    if abs(p0) < 0.001:
        print("\nWARNING: initial position is 0. The retain test is more")
        print("informative if the reel is at a non-zero position. Jog it")
        print("a few mm manually, then re-run.")

    p_after_ss = run_scenario("STOP + START", STOP_START, p0)
    base_for_warm = p_after_ss if p_after_ss is not None else p0
    p_after_warm = run_scenario("WARM RESET", WARM_RESET, base_for_warm)
    base_for_cold = p_after_warm if p_after_warm is not None else (p_after_ss if p_after_ss is not None else p0)
    run_scenario("COLD RESET", COLD_RESET, base_for_cold)

    print()
    print("Decision input for A.1:")
    print("  - If STOP+START + WARM keep position -> retained assumption OK")
    print("    for renderer-side crash and dev-cycle scenarios.")
    print("  - If COLD wipes position -> document that production cold")
    print("    resets need a re-home step to re-establish reel origin.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
