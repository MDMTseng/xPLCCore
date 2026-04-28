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
#   PH#8: ST_CHG emission is deferred via PendingStChgFromState so a
#         transient ring-full doesn't lose the event (same shape as
#         PH#5). We pin the var exists, reads -1 (sentinel) in steady
#         state, and that StateChangeEventCount still climbs across a
#         real FSM transition.
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


def test_pending_st_chg_from_state_idle(fsm_ready):
    """PH#8: PendingStChgFromState must exist and read -1 (sentinel for
    "no edge waiting") in steady state. A non-(-1) reading would mean a
    state change is stuck unable to publish, or the ring is full and
    the retry pump is starving -- both are bugs."""
    vals = _read_symbols(["AxisGroupSM.PendingStChgFromState"])
    v = vals.get("AxisGroupSM.PendingStChgFromState", "")
    assert not v.startswith("ERR:"), (
        f"PendingStChgFromState not readable -- PH#8 var was renamed/removed: {v}"
    )
    # Sentinel is -1; any other value means an emit is pending.
    assert v == "-1", (
        f"PendingStChgFromState={v}, expected -1 in idle state -- a "
        f"deferred ST_CHG never landed"
    )


def test_tx_stall_reset_counter_present_and_quiet(fsm_ready):
    """TX-side half-open detection: GVL.TxStallResetCount must be readable
    and stay 0 under normal operation. A regression that re-introduces
    the infinite head-drop loop (without escalating to socket reset)
    would leave the counter at 0 only because the threshold was never
    hit, but more importantly a regression that *removes* the counter
    would surface as ERR: here. Pre-fix sustained TX stall would churn
    SendStallDropCount forever; post-fix it escalates to a socket reset
    after TX_STALL_DROP_THRESHOLD consecutive drops."""
    vals = _read_symbols([
        "GVL.TxStallResetCount",
        "TCP_MSGPAK_Server.consecutive_stall_drops",
        "TCP_MSGPAK_Server.TX_STALL_DROP_THRESHOLD",
    ])
    v = vals.get("GVL.TxStallResetCount", "")
    assert not v.startswith("ERR:"), (
        f"TxStallResetCount not readable -- TX-stall escalation regressed: {v}"
    )
    # Healthy link: no TX stall in flight.
    drops = vals.get("TCP_MSGPAK_Server.consecutive_stall_drops", "")
    assert drops == "0", (
        f"consecutive_stall_drops={drops}, expected 0 -- the TX path "
        f"is currently mid-stall during the test, link is unhealthy"
    )
    thr = int(vals.get("TCP_MSGPAK_Server.TX_STALL_DROP_THRESHOLD", "0"))
    assert thr > 0, (
        f"TX_STALL_DROP_THRESHOLD={thr}; threshold must be positive or "
        f"escalation never fires"
    )


def test_a3_supervisor_does_not_trip_on_idle_ready(fsm_ready):
    """A3 self-keepalive: with motion buffer empty, the UiHeartbeatStale
    watchdog must not fire even if PING goes quiet for longer than
    UI_HEARTBEAT_TIMEOUT_MS. Pre-fix the gate was just (Ready AND age >
    timeout), which tripped a backgrounded-UI scenario into Error.
    Post-fix the gate also requires MotionBufferSize > 0, so idle Ready
    is safe."""
    pre = _read_symbols([
        "GVL.UiHeartbeatStaleCount",
        "GVL.UI_HEARTBEAT_TIMEOUT_MS",
        "AxisGroupSM.MotionBufferSize",
    ])
    timeout_ms = int(pre.get("GVL.UI_HEARTBEAT_TIMEOUT_MS", "5000"))
    pre_count = int(pre["GVL.UiHeartbeatStaleCount"])
    pre_buf = int(pre.get("AxisGroupSM.MotionBufferSize", "0"))
    assert pre_buf == 0, (
        f"Pre-condition: motion buffer must be empty, got {pre_buf}"
    )
    # Wait > timeout so even pre-fix would have tripped if UI stayed silent.
    # The harness UI keeps pinging so this isn't a true silence test, but
    # we're pinning that the gate logic doesn't bump the counter under
    # normal idle. A regression that drops MotionBufferSize > 0 from the
    # gate would surface as spurious counter growth.
    time.sleep((timeout_ms / 1000.0) + 2.0)
    post = _read_symbols([
        "GVL.UiHeartbeatStaleCount",
        "AxisGroupSM.AxisGroupManagerFb._eState",
    ])
    post_count = int(post["GVL.UiHeartbeatStaleCount"])
    state = post.get("AxisGroupSM.AxisGroupManagerFb._eState", "?")
    assert post_count == pre_count, (
        f"UiHeartbeatStaleCount climbed {pre_count} -> {post_count} "
        f"during idle Ready; A3 gate is firing without motion in flight"
    )
    assert "Ready" in state, (
        f"FSM left Ready during idle wait (state={state}); A3 likely tripped"
    )


def test_audit_fix_counters_present(fsm_ready):
    """Audit pass added three counters: SelfReentryCount (re-running entry
    actions on same-state EV_ERROR/EV_RESET), PendingMoveDoneDropCount and
    PendingStChgDropCount (force-dropping deferred events when the reMP
    ring stays full past PENDING_RETRY_MAX_SCANS). All three must read as
    UDINTs from the online API; a regression that renames or removes them
    surfaces as ERR: here. We don't pin specific values -- SelfReentryCount
    legitimately climbs under stacked faults, and the drop counters should
    be 0 on a healthy link but a slow consumer can plausibly bump them."""
    vals = _read_symbols([
        "GVL.SelfReentryCount",
        "GVL.PendingMoveDoneDropCount",
        "GVL.PendingStChgDropCount",
        "AxisGroupSM.PENDING_RETRY_MAX_SCANS",
    ])
    for sym in ("GVL.SelfReentryCount",
                "GVL.PendingMoveDoneDropCount",
                "GVL.PendingStChgDropCount"):
        v = vals.get(sym, "")
        assert not v.startswith("ERR:"), f"{sym} not readable: {v}"
        # All three are UDINTs; just sanity-check parse.
        n = int(v)
        assert n >= 0, f"{sym}={n} negative"
    thr = int(vals.get("AxisGroupSM.PENDING_RETRY_MAX_SCANS", "0"))
    assert thr > 0, (
        f"PENDING_RETRY_MAX_SCANS={thr}; the bound must be positive or "
        f"the retry pump never escapes a stuck ring"
    )


def test_dedupe_ring_clears_across_error_recovery(fsm_ready):
    """Audit-fix: the G1 dedupe ring (RecentCmdIds[]) must be wiped when
    the FSM enters Error or UnInited. Without this, after a fault recovery
    the next G1 with a CommandId that happens to collide with a pre-fault
    cached id would replay a stale movement_id (or worse, the cached
    movement_id corresponds to a coord-gate-failed command that never ran
    at all). Verifies by reading the ring head after EV_ERROR -> EV_RESET
    and confirming it's back at 0 with cleared slots."""
    # Drive the fault path. fsm_ready left us in Ready with coord gate up.
    tms.fire_event(9, "EV_ERROR", 0.6)
    # Ring should be cleared on Error entry. Read before recovery so a
    # regression that only clears on UnInited is also caught.
    in_error = _read_symbols([
        "AxisGroupSM.RecentCmdRingHead",
        "AxisGroupSM.RecentCmdIds[0]",
        "AxisGroupSM.RecentCmdIds[15]",
        "AxisGroupSM.RecentCmdMoveIds[0]",
        "AxisGroupSM.AxisGroupManagerFb._eState",
    ])
    state = in_error.get("AxisGroupSM.AxisGroupManagerFb._eState", "?")
    assert "Error" in state, f"failed to drive Error: state={state}"
    head = in_error.get("AxisGroupSM.RecentCmdRingHead", "?")
    assert head == "0", (
        f"RecentCmdRingHead={head} after Error entry, expected 0 -- "
        f"dedupe ring was not cleared on fault"
    )
    for slot in ("AxisGroupSM.RecentCmdIds[0]",
                 "AxisGroupSM.RecentCmdIds[15]",
                 "AxisGroupSM.RecentCmdMoveIds[0]"):
        v = in_error.get(slot, "?")
        assert v == "0", (
            f"{slot}={v} after Error entry, expected 0 -- ring slot "
            f"survived the clear pass"
        )

    # Recover so subsequent tests start from Ready+coord.
    tms.fire_event(tms.EV_RESET, "EV_RESET", 0.4)
    tms.fire_event(tms.EV_POWER_ON, "EV_POWER_ON", 1.0)
    tms.fire_event(tms.EV_GROUP_ENABLE, "EV_GROUP_ENABLE", 1.0)
    tms.fire_event(tms.EV_HOME_GO_FORCE_SKIP, "EV_HOME_GO_FORCE_SKIP", 0.6)
    tms.wait_for_state(tms.READY, timeout=8.0, label="Ready (recover)")
    tms.harness_send({"type": "M", "cmd": "SetCoord0"}, timeout=3.0)


def test_st_chg_event_count_advances_on_transition(fsm_ready):
    """PH#8: even with the inline drop site removed, real state changes
    must still bump GVL.StateChangeEventCount. EV_ERROR forces a Ready
    -> Error edge; we then recover so subsequent tests start clean."""
    pre = _read_symbols(["GVL.StateChangeEventCount"])
    pre_n = int(pre["GVL.StateChangeEventCount"])
    tms.fire_event(9, "EV_ERROR", 0.6)
    post = _read_symbols(["GVL.StateChangeEventCount"])
    post_n = int(post["GVL.StateChangeEventCount"])
    assert post_n > pre_n, (
        f"StateChangeEventCount did not advance ({pre_n} -> {post_n}) "
        f"across EV_ERROR transition -- retry pump never published"
    )
    # Recover for next test.
    tms.fire_event(tms.EV_RESET, "EV_RESET", 0.4)
    tms.fire_event(tms.EV_POWER_ON, "EV_POWER_ON", 1.0)
    tms.fire_event(tms.EV_GROUP_ENABLE, "EV_GROUP_ENABLE", 1.0)
    tms.fire_event(tms.EV_HOME_GO_FORCE_SKIP, "EV_HOME_GO_FORCE_SKIP", 0.6)
    tms.wait_for_state(tms.READY, timeout=8.0, label="Ready (recover)")
    tms.harness_send({"type": "M", "cmd": "SetCoord0"}, timeout=3.0)
