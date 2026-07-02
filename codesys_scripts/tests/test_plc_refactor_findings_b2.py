"""BATCH 2 acceptance tests for doc_review/refactor_analysis_2026-07-02.md.

Same test-first contract as test_plc_refactor_findings.py (R1-R11): each
fixable finding gets a wire-level acceptance test BEFORE the fix lands.
Tests expected red against the pre-fix PLC turn green after the fix with
zero test edits. Mapping (Bn == batch-2 item, section == analysis doc):

    B1  G4 premature/duplicate ACK: exactly ONE reply per G4 id -- the
        old code set ShouldReturn/CommandAck at branch entry, so a
        CommandAccepted=FALSE G4 acked early AND re-acked after every
        100ms retry cooldown.                              [analysis 1.5]
    B2c GroupActualPositionFb.Valid gate must not over-suppress: a
        distance trigger centred on the CURRENT arm position must fire
        while the position read is valid.                  [analysis 1.6]
        (B2a/B2b -- trackConveyorBeltFb / SetCoordTransformFb .Error
        consumers -- are code-review criteria per test_standards 3;
        their error conditions can't be forced over the wire.)
    B3  Coord1 dual-bind unification: SYS COORD1_BIND and the FlyEvent
        coord1_bind action must reject the SAME invalid inputs through
        the SAME validation (Coord1CommitBind). SYS path NAKs; fly path
        takes the fault path (FSM leaves Ready + COORD1_ERROR), never a
        silent no-op or -- worse -- a silent bind under queued motion.
                                                           [analysis 2.1]
    B4  Fly-bind fault must use AxisGroupManagerFb.Transition, not the
        InputEvent level-latch: a SECOND fault after an operator reset
        produced no edge, so the FSM stayed Ready while the arm kept
        moving.                                            [analysis 1.4]

Ordering note: B3 fault tests and B4 perturb FSM state and run after the
read-mostly B1/B2c tests. All fault tests self-clean via GA_EV EV_RESET.

Env prereqs: live PLC at 192.168.1.70:8125, UI reachable via remote_ctrl
(single-client arbitration), CODESYS RPC daemon for virtual_motors gate.
Run: pytest codesys_scripts/tests/test_plc_refactor_findings_b2.py -v
"""
import time

import pytest

from tests._raw_plc import (
    raw_plc_socket, send_pack, drain_until_id, drain_all,
    bring_fsm_to_ready, m4_pulse_seq,
)

EV_RESET, EV_ERROR = 8, 9
ST_READY, ST_ERROR = 70, 990

NAK_WAIT_S = 2.5


def _send_recv(s, payload, pid, timeout=NAK_WAIT_S):
    payload = dict(payload, id=pid)
    send_pack(s, payload)
    return drain_until_id(s, pid, timeout)


def _machine_state(s, pid):
    return _send_recv(s, {"type": "SYS", "cmd": "GET_MACHINE_STATE"}, pid)


def _get_diag(s, pid):
    return _send_recv(s, {"type": "SYS", "cmd": "GET_DIAG"}, pid)


def _coord1_debug(s, pid):
    return _send_recv(s, {"type": "SYS", "cmd": "GET_COORD1_DEBUG"}, pid)


def _to_ready(s, base_id, attempts=2):
    """bring_fsm_to_ready with one retry: after an EV_RESET that aborted
    in-flight motion the SMC group occasionally needs a second climb
    (observed live during the red-baseline run)."""
    for i in range(attempts):
        if bring_fsm_to_ready(s, base_id=base_id + i * 50):
            return True
        time.sleep(1.0)
    return False


def _ready_with_coord(s, base_id):
    assert _to_ready(s, base_id), "could not reach Ready"
    a = _send_recv(s, {"type": "M", "cmd": "SetCoord0"}, base_id + 500)
    assert a and a.get("ack") is True, "SetCoord0 not acked: %r" % a


def _unbind(s, pid):
    r = _send_recv(s, {"type": "SYS", "cmd": "COORD1_UNBIND"}, pid)
    assert r and r.get("ack") is True, "COORD1_UNBIND failed: %r" % r


def _pulse_now(s, pid):
    d = _coord1_debug(s, pid)
    assert d is not None, "GET_COORD1_DEBUG timed out"
    return d["pulse_raw"]


def _sys_bind(s, pid, ref_pulse, scale_x=100.0):
    return _send_recv(s, {
        "type": "SYS", "cmd": "COORD1_BIND",
        "ref_pulse": ref_pulse,
        "ref_xyz": [0.0, 0.0, -100.0],
        "scale": [scale_x, 0.0, 0.0],
    }, pid)


def _queue_slow_moves(s, base_id):
    """Queue a slow in-workspace G1 chain (same envelope as R10). Returns
    True if the buffer is observably non-empty afterwards."""
    for i, z in enumerate([-80.0, -95.0, -110.0, -125.0]):
        r = _send_recv(s, {"type": "M", "cmd": "G1",
                           "X": 0, "Y": 0, "Z": z, "F": 10.0}, base_id + i)
        assert r and r.get("ack") is True, "G1 %d not acked: %r" % (i, r)
    st = _machine_state(s, base_id + 100)
    return bool(st) and st.get("motion_buffer_size", 0) > 0


def _fly_bind_m4(s, pid, pulse_target, scale_x=100.0, exit_offset=200000,
                 ttl_ms=5000, event_id=4242):
    """Register an M4 FlyEvent coord1_bind. With pulse_target <= current
    ConveyorPulseRaw the PulseTrigger fires on the next Ready scan."""
    return _send_recv(s, {
        "type": "M", "cmd": "M4", "action": "coord1_bind",
        "trig": 130, "pulse_target": pulse_target,
        "ref_xyz": [0.0, 0.0, -100.0],
        "scale": [scale_x, 0.0, 0.0],
        "exit_pulse_offset": exit_offset,
        "ttl_ms": ttl_ms, "event_id": event_id,
    }, pid)


def _wait_state(s, want_st, base_id, timeout_s=3.0):
    """Poll GET_MACHINE_STATE until st == want_st. Returns last seen st."""
    deadline = time.time() + timeout_s
    st = None
    while time.time() < deadline:
        r = _machine_state(s, base_id)
        base_id += 1
        if r is not None:
            st = r.get("st")
            if st == want_st:
                return st
        time.sleep(0.2)
    return st


# ─── B1: G4 must produce exactly one reply per id ───────────────────

def test_B1_g4_exactly_one_reply(virtual_motors_forced, raw_plc_socket):
    """The G4 branch used to set ShouldReturn/CommandAck := TRUE at branch
    ENTRY -- before SMC_GroupWait ran. On CommandAccepted=FALSE the packet
    stayed queued but the tail-commit emitted ack:TRUE anyway (without
    movement_id), then the 100ms retry cooldown re-processed the same id
    and emitted again (one reply per ~100ms for the refusal's duration).
    Contract (mirrors G1): reply only from the accepted path -- exactly
    one reply per id. Refusal can't be forced over the wire, so this test
    guards the single-reply invariant on the accept path (code review
    covers the refusal branch, per test_standards 3)."""
    s = raw_plc_socket
    assert _to_ready(s, 92000), "could not reach Ready"
    drain_all(s, 0.3)  # flush stale events
    send_pack(s, {"type": "M", "cmd": "G4", "P": 0.05, "id": 92600})
    replies = [o for o in drain_all(s, 2.5)
               if isinstance(o, dict) and o.get("id") == 92600]
    assert len(replies) == 1, (
        "G4 id=92600 produced %d replies (expected exactly 1): %r"
        % (len(replies), replies))
    assert replies[0].get("ack") is True, "G4 not acked: %r" % replies[0]
    assert replies[0].get("movement_id"), (
        "accepted G4 reply carries no movement_id (premature ack before "
        "SMC_GroupWait ran): %r" % replies[0])


# ─── B2c: position-valid gate must not over-suppress ────────────────

def test_B2c_distance_trigger_fires_when_position_valid(
        virtual_motors_forced, raw_plc_socket):
    """GroupActualPositionFb.Error/.Valid feed the DistanceTrigger
    evaluation. The fix gates trigger evaluation on Valid AND NOT Error;
    this test proves the gate does not over-suppress: a distance trigger
    centred on wherever the arm currently reads MUST fire while the
    position is valid (io_trig_count += 2: set stage + auto-reset stage).
    Green pre-fix (no gate existed) and green post-fix (gate open on a
    healthy group)."""
    s = raw_plc_socket
    _ready_with_coord(s, 93000)
    d = _coord1_debug(s, 93600)
    assert d is not None, "GET_COORD1_DEBUG timed out"
    pre = _get_diag(s, 93610)
    assert pre is not None
    r = _send_recv(s, {
        "type": "M", "cmd": "M4", "motion_id": 0, "trig": 120,
        "tx": d["arm_x"], "ty": d["arm_y"], "tz": d["arm_z"],
        "td": 25.0, "tin": 1,
        "pin_op_seq": m4_pulse_seq(0x4000, 0x4000, reset_ms=50),
        "ttl_ms": 3000, "event_id": 24242,
    }, 93620)
    assert r and r.get("ack") is True, "M4 distance-trigger not acked: %r" % r
    time.sleep(0.6)
    post = _get_diag(s, 93630)
    assert post is not None
    delta = post.get("io_trig_count", 0) - pre.get("io_trig_count", 0)
    assert delta == 2, (
        "distance trigger at current arm position (%.1f,%.1f,%.1f) did not "
        "fire set+reset while position valid (io_trig_count delta=%d, "
        "expected 2) -- gate over-suppresses"
        % (d["arm_x"], d["arm_y"], d["arm_z"], delta))


# ─── B3: SYS / fly bind parity ──────────────────────────────────────

def test_B3_sys_bind_rejects_scale_zero(virtual_motors_forced,
                                        raw_plc_socket):
    """SYS side of the parity contract: scale[0]=0 NAKs. (Green pre-fix;
    pins the behavior the shared Coord1CommitBind must preserve.)"""
    s = raw_plc_socket
    assert _to_ready(s, 94000), "could not reach Ready"
    _unbind(s, 94600)
    r = _sys_bind(s, 94610, ref_pulse=0, scale_x=0.0)
    assert r is not None, "COORD1_BIND (scale 0) silently dropped"
    assert r.get("ack") is False
    assert r.get("err") == "belt_scale_x_required", "unexpected err: %r" % r


def test_B3_sys_bind_rejects_rebind(virtual_motors_forced, raw_plc_socket):
    """SYS side: re-bind while bound NAKs rebind_requires_unbind. (Green
    pre-fix; parity pin.)"""
    s = raw_plc_socket
    assert _to_ready(s, 94700), "could not reach Ready"
    _unbind(s, 94710)
    pulse = _pulse_now(s, 94720)
    try:
        ok = _sys_bind(s, 94730, ref_pulse=pulse)
        assert ok and ok.get("ack") is True, "first bind failed: %r" % ok
        r = _sys_bind(s, 94740, ref_pulse=pulse)
        assert r is not None
        assert r.get("ack") is False
        assert r.get("err") == "rebind_requires_unbind", (
            "unexpected err: %r" % r)
    finally:
        _unbind(s, 94750)


def test_B3_sys_bind_rejects_busy(virtual_motors_forced, raw_plc_socket):
    """SYS side: COORD1_BIND with motion queued NAKs coord_bind_busy.
    (Green pre-fix; this is the check the fly path was missing.)"""
    s = raw_plc_socket
    _ready_with_coord(s, 95000)
    _unbind(s, 95600)
    try:
        if not _queue_slow_moves(s, 95610):
            pytest.skip("virtual axes drained the chain before the bind "
                        "could race it -- rerun on slower F")
        pulse = _pulse_now(s, 95700)
        r = _sys_bind(s, 95710, ref_pulse=pulse)
        assert r is not None, "COORD1_BIND under motion silently dropped"
        assert r.get("ack") is False
        assert r.get("err") == "coord_bind_busy", "unexpected err: %r" % r
    finally:
        _send_recv(s, {"type": "SYS", "cmd": "GA_EV", "ev": EV_RESET}, 95800)
        time.sleep(0.4)


def test_B3_fly_bind_while_motion_queued_faults(virtual_motors_forced,
                                                raw_plc_socket):
    """Fly side of the SAME invalid input: a coord1_bind FlyEvent firing
    while motion is queued must take the fault path (FSM leaves Ready,
    bind NOT applied) -- pre-fix the fly path had no MotionBufferSize
    check and silently bound, re-anchoring PCS_1 under the in-flight
    move (exactly the mid-flight-frame-change the SYS path refuses)."""
    s = raw_plc_socket
    _ready_with_coord(s, 96000)
    _unbind(s, 96600)
    try:
        if not _queue_slow_moves(s, 96610):
            pytest.skip("virtual axes drained the chain before the fly "
                        "bind could race it -- rerun on slower F")
        pulse = _pulse_now(s, 96700)
        r = _fly_bind_m4(s, 96710, pulse_target=pulse, event_id=9631)
        assert r and r.get("ack") is True, "fly-bind M4 not acked: %r" % r
        st = _wait_state(s, ST_ERROR, 96720, timeout_s=3.0)
        d = _coord1_debug(s, 96790)
        assert st == ST_ERROR, (
            "fly bind fired with motion queued but FSM stayed st=%r "
            "(expected Error %d) -- silent bind under in-flight motion "
            "(bound=%r)" % (st, ST_ERROR, d and d.get("bound")))
        assert d is not None and d.get("bound") is False, (
            "fault path must not leave the bind applied: %r" % d)
    finally:
        _send_recv(s, {"type": "SYS", "cmd": "GA_EV", "ev": EV_RESET}, 96800)
        time.sleep(0.4)


def test_B3_fly_bind_scale_zero_faults(virtual_motors_forced,
                                       raw_plc_socket):
    """Fly side: scale[0]=0 (invalid on the SYS path) must fault loudly
    through the shared validation -- FSM leaves Ready, no bind."""
    s = raw_plc_socket
    _ready_with_coord(s, 97000)
    _unbind(s, 97600)
    try:
        pulse = _pulse_now(s, 97610)
        r = _fly_bind_m4(s, 97620, pulse_target=pulse, scale_x=0.0,
                         event_id=9632)
        assert r and r.get("ack") is True, "fly-bind M4 not acked: %r" % r
        st = _wait_state(s, ST_ERROR, 97630, timeout_s=3.0)
        assert st == ST_ERROR, (
            "malformed fly bind (scale=0) did not drive FSM to Error "
            "(st=%r) -- fault swallowed" % st)
        d = _coord1_debug(s, 97690)
        assert d is not None and d.get("bound") is False
    finally:
        _send_recv(s, {"type": "SYS", "cmd": "GA_EV", "ev": EV_RESET}, 97700)
        time.sleep(0.4)


# ─── B4: second fly-bind fault must still trip the FSM ──────────────

def test_B4_fly_fault_twice_leaves_ready_both_times(virtual_motors_forced,
                                                    raw_plc_socket):
    """ProcessFlyEventsAndIo reported fly-bind faults by writing
    InputEvent := EV_ERROR. The only consumer is the edge detector in
    UpdateRuntimeAndInputEvent and nothing ever resets InputEvent back to
    EV_NONE (GA_EV bypasses it by design), so after fault #1 + operator
    EV_RESET the second fault wrote the same value -- no edge, FSM stayed
    Ready, arm kept moving. Contract: the fault path calls
    AxisGroupManagerFb.Transition(EV_ERROR) directly; the FSM must leave
    Ready on EVERY fault, not just the first. Fault used here: fly
    rebind-while-bound (exists pre- and post-fix)."""
    s = raw_plc_socket
    for rnd in (1, 2):
        base = 98000 + rnd * 400
        _ready_with_coord(s, base)
        _unbind(s, base + 100)
        pulse = _pulse_now(s, base + 110)
        ok = _sys_bind(s, base + 120, ref_pulse=pulse)
        assert ok and ok.get("ack") is True, (
            "round %d: SYS bind failed: %r" % (rnd, ok))
        r = _fly_bind_m4(s, base + 130, pulse_target=pulse,
                         event_id=9640 + rnd)
        assert r and r.get("ack") is True, (
            "round %d: fly-bind M4 not acked: %r" % (rnd, r))
        st = _wait_state(s, ST_ERROR, base + 140, timeout_s=3.0)
        try:
            assert st == ST_ERROR, (
                "round %d: fly rebind fault did NOT drive FSM to Error "
                "(st=%r) -- %s" % (rnd, st,
                                   "level-latched InputEvent swallowed the "
                                   "second edge" if rnd == 2 else
                                   "fault path broken outright"))
        finally:
            _send_recv(s, {"type": "SYS", "cmd": "GA_EV", "ev": EV_RESET},
                       base + 300)
            time.sleep(0.4)
