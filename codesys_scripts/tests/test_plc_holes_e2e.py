# Regression tests for the PLC conceptual-hole fixes (PH#1/#3/#5/#6).
# Each test pins a specific behaviour change made on 2026-04-28:
#
#   PH#1: EC_Task no longer calls reMP_info_ridx.consumeTail; producer-side
#         drop-newest. We can't directly trigger ring-full on a healthy
#         system, but we sanity-check that ReMpDropCount is monotonic and
#         rapid SYS bursts continue to receive replies.
#   PH#3: M4 with ttl_ms > MAX_DINT clamps TimeToLiveMs to -1 (no expiry)
#         instead of silently wrapping to a tiny negative DINT.
#   PH#5: MOVE_DONE emission is deferred via PendingMoveDoneId so a
#         transient ring-full doesn't lose the event. We pin the var
#         exists and reads back as 0 in steady state (post-emit).
#   PH#6: GVL.CoordSystemConfigured clears on Error entry (not just
#         UnInited), so an in-place SMC-reset recovery doesn't leave a
#         stale gate open.
import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import test_movement_sequence as tms


HERE = Path(__file__).resolve().parent
RPC = HERE.parent / "rpc.py"


def _read_symbols(symbols: list[str]) -> dict:
    """Run an inline daemon job that logs into the running app, reads
    each symbol, and prints `key=value` lines we parse back."""
    syms_repr = repr(symbols)
    job = textwrap.dedent(f"""
        proj = projects.primary
        app = proj.active_application
        oapp = online.create_online_application(app)
        oapp.login(OnlineChangeOption.Try, False)
        try:
            for sym in {syms_repr}:
                try:
                    v = oapp.read_value(sym)
                except Exception as ex:
                    v = "ERR:" + str(ex)[:60]
                print("%s=%s" % (sym, v))
        finally:
            try: oapp.logout()
            except Exception: pass
    """)
    tmp = HERE / "_inline_read.py"
    tmp.write_text(job, encoding="ascii")
    try:
        res = subprocess.run(
            [sys.executable, str(RPC), "exec", "--file", str(tmp)],
            capture_output=True, text=True, timeout=30,
        )
    finally:
        try: tmp.unlink()
        except OSError: pass
    out = {}
    for line in (res.stdout or "").splitlines():
        if "=" in line and not line.startswith("["):
            k, _, v = line.partition("=")
            v = v.strip()
            # CODESYS read_value returns "TYPE#value" for typed scalars
            # (e.g. "ULINT#0", "UDINT#42"). Strip the type prefix so
            # callers can int()/bool()/str-compare cleanly.
            if "#" in v:
                v = v.split("#", 1)[1]
            out[k.strip()] = v
    return out


def test_remp_drop_counter_monotonic_under_burst(fsm_ready):
    """PH#1: rapid SYS bursts must keep being acked; drop counter only
    grows. No assertion that drops==0 (a slow consumer scan can legit
    drop), but a regression that re-introduces consumeTail in EC_Task
    would corrupt slots and produce malformed replies long before this
    test ever ran."""
    before = _read_symbols(["GVL.ReMpDropCount"])
    drops_before = int(before.get("GVL.ReMpDropCount", "0"))
    acked = 0
    for _ in range(40):
        r = tms.harness_send({"type": "SYS", "cmd": "PING"}, timeout=2.0)
        if r.get("pong"):
            acked += 1
    after = _read_symbols(["GVL.ReMpDropCount"])
    drops_after = int(after.get("GVL.ReMpDropCount", "0"))
    assert acked >= 30, f"only {acked}/40 PINGs acked; PLC reply path is sick"
    assert drops_after >= drops_before, (
        f"ReMpDropCount went BACKWARDS ({drops_before} -> {drops_after}); "
        f"counter is not monotonic"
    )


def test_flyevent_ttl_raw_var_present(fsm_ready):
    """PH#3: the clamp stages ttl_ms through FlyEventTtlRaw : LINT
    before the DINT cast. We can't easily round-trip an M4 without
    coordinating motion buffer state, so we pin the staging var
    exists as a LINT readable via the online API. A regression that
    deletes the var (or reverts to the inline TO_DINT cast) will
    surface as ERR: here, AND will be caught at build time because
    ProcessMotionPacket references FlyEventTtlRaw."""
    vals = _read_symbols(["AxisGroupSM.FlyEventTtlRaw"])
    v = vals.get("AxisGroupSM.FlyEventTtlRaw", "")
    assert not v.startswith("ERR:"), (
        f"FlyEventTtlRaw not readable -- PH#3 staging var was removed: {v}"
    )
    # The var is overwritten on each M4 unpack and not reset between
    # commands, so its current value reflects the last M4 ttl_ms seen.
    # Whatever the value, it must fit in LINT (not get truncated like
    # DINT would for >2^31 values). Sanity-check the int parse succeeds
    # and the magnitude is within LINT range.
    n = int(v)
    assert -(2**63) <= n < 2**63, f"FlyEventTtlRaw={n} outside LINT range"


def test_pending_move_done_id_exists_and_idle(fsm_ready):
    """PH#5: PendingMoveDoneId must exist on the FB and read 0 in
    steady state (no in-flight motion, no dropped MOVE_DONE waiting to
    re-emit). A binding-rename in the FB declaration would surface as
    an ERR: read here."""
    vals = _read_symbols(["AxisGroupSM.PendingMoveDoneId"])
    v = vals.get("AxisGroupSM.PendingMoveDoneId", "")
    assert not v.startswith("ERR:"), (
        f"PendingMoveDoneId not readable -- PH#5 var was renamed/removed: {v}"
    )
    # In steady state with no recent MOVE_DONE drop, the latch is 0.
    assert v == "0", (
        f"PendingMoveDoneId={v}, expected 0 in idle state -- a MOVE_DONE "
        f"may have been dropped on the previous test and never retried"
    )


def test_coord_cleared_on_error_entry(fsm_ready):
    """PH#6: forcing the FSM into Error must clear CoordSystemConfigured,
    even without an EV_RESET -> UnInited cycle. Pre-fix the gate stayed
    TRUE through Error and any Ready-recovery would let G1 fire against
    stale homing state."""
    # fsm_ready already SetCoord0'd, so CoordSystemConfigured = TRUE.
    pre = _read_symbols(["GVL.CoordSystemConfigured"])
    assert pre.get("GVL.CoordSystemConfigured") == "TRUE", (
        f"Pre-condition failed: gate not set after fsm_ready; got "
        f"{pre.get('GVL.CoordSystemConfigured')!r}"
    )
    # Drive Error: EV_ERROR = 9 in E_RobotEvent.
    tms.fire_event(9, "EV_ERROR", 0.6)
    post = _read_symbols([
        "GVL.CoordSystemConfigured",
        "AxisGroupSM.AxisGroupManagerFb._eState",
    ])
    state = post.get("AxisGroupSM.AxisGroupManagerFb._eState", "?")
    gate = post.get("GVL.CoordSystemConfigured", "?")
    assert gate == "FALSE", (
        f"CoordSystemConfigured still {gate} after Error transition "
        f"(state={state}); PH#6 fix regressed"
    )
    # Recover so the next test in the session starts from Ready+coord.
    tms.fire_event(tms.EV_RESET, "EV_RESET", 0.4)
    tms.fire_event(tms.EV_POWER_ON, "EV_POWER_ON", 1.0)
    tms.fire_event(tms.EV_GROUP_ENABLE, "EV_GROUP_ENABLE", 1.0)
    tms.fire_event(tms.EV_HOME_GO_FORCE_SKIP, "EV_HOME_GO_FORCE_SKIP", 0.6)
    tms.wait_for_state(tms.READY, timeout=8.0, label="Ready (recover)")
    tms.harness_send({"type": "M", "cmd": "SetCoord0"}, timeout=3.0)
