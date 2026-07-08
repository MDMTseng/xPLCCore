"""Red-baseline acceptance tests for doc_review/refactor_analysis_2026-07-02.md.

Test-first contract (user directive 2026-07-02): every fixable finding gets
a wire-level acceptance test BEFORE the fix lands. Tests in this file are
EXPECTED TO FAIL (red) against the pre-fix PLC -- each failure is the bug
demonstrating itself. After the corresponding fix, the test must pass
unchanged. Mapping (Rn == test, Fn == finding in the analysis doc):

    R1  F-GA_EV      GA_EV must NAK sparse-enum gaps (1,3,5) and internal
                     events (EV_OK=10, EV_ER=11)          [analysis 4.1]
    R2  F-unknown    unknown M cmd NAK must carry an err string [1.7]
    R3  F-m4gate     M4 coord1_bind with non-pulse trig must NAK,
                     not silently vanish                   [1.2 row 1]
    R4  F-m4gate     M4 pin_op with zero net stages must NAK [1.2 row 1]
    R5  F-wft        WAIT_FOR_TRIGGER_MOTION_PROGRESS refusal must NAK,
                     not silently vanish                   [1.2 row 2]
    R6  F-idsent     two id-less G1s must BOTH execute (id sentinel
                     -1 vs 0 dedupe false-match)           [1.3]
    R7  F-spwrap     SCRATCHPAD_WRITE must range-NAK values > 2^31-1
                     instead of silently wrapping + ACK    [1.7]
    R8  F-rstdrift   SYS RESET_DBG_INFO must zero EVERY counter GET_DIAG
                     publishes (canonical-list contract)   [2.2]
    R9  F-dupkey     NAK replies must not contain duplicate msgpack map
                     keys ('ack' packed twice)             [1.7]
    R10 F-movedone   group abort must NOT emit MOVE_DONE for the aborted
                     move nor advance last_completed_movement_id [1.1]
    R11 F-wedge      M4 under FlyEvent slot saturation must NAK
                     flyevent_buffer_full, not wedge the inbound queue
                     (queue must keep draining)            [1.2 row 3]

Ordering note: R10/R11 perturb FSM state / fly-event slots and run last
(pytest runs file order). Both self-clean (EV_RESET / TTL expiry).

Env prereqs: live PLC at 192.168.1.70:8125, UI reachable via remote_ctrl
(single-client arbitration), CODESYS RPC daemon for virtual_motors gate.
Run: pytest codesys_scripts/tests/test_plc_refactor_findings.py -v
"""
import time

import msgpack
import pytest

from tests._raw_plc import (
    raw_plc_socket, send_pack, drain_until_id, drain_all,
    bring_fsm_to_ready, ui_set_tcp,
)

EV_RESET, EV_ERROR = 8, 9
ST_READY, ST_ERROR, ST_UNINIT = 70, 990, 10

# Silent-drop probes: how long we give the PLC to prove it replies.
# Generous vs the ~10ms real reply path so a red is unambiguous.
NAK_WAIT_S = 2.5


def _send_recv(s, payload, pid, timeout=NAK_WAIT_S):
    payload = dict(payload, id=pid)
    send_pack(s, payload)
    return drain_until_id(s, pid, timeout)


def _machine_state(s, pid):
    return _send_recv(s, {"type": "SYS", "cmd": "GET_MACHINE_STATE"}, pid)


def _get_diag(s, pid):
    return _send_recv(s, {"type": "SYS", "cmd": "GET_DIAG"}, pid)


def _arm_z(s, pid):
    r = _send_recv(s, {"type": "SYS", "cmd": "GET_COORD1_DEBUG"}, pid)
    return None if r is None else r.get("arm_z")


def _ready_with_coord(s, base_id):
    assert bring_fsm_to_ready(s, base_id=base_id), "could not reach Ready"
    a = _send_recv(s, {"type": "M", "cmd": "SetCoord0"}, base_id + 500)
    assert a and a.get("ack") is True, "SetCoord0 not acked: %r" % a


def _wait_motion_drained(s, base_id, timeout_s=20.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        st = _machine_state(s, base_id)
        base_id += 1
        if st and st.get("motion_buffer_size", 1) == 0:
            return True
        time.sleep(0.3)
    return False


def _wait_arm_z(s, target_z, base_id, timeout_s=25.0, tol=0.5):
    """Poll GET_COORD1_DEBUG arm_z until it settles at target_z. Returns
    the last observed arm_z (may be off-target on timeout -- caller
    asserts)."""
    deadline = time.time() + timeout_s
    z = None
    while time.time() < deadline:
        z = _arm_z(s, base_id)
        base_id += 1
        if z is not None and abs(z - target_z) < tol:
            return z
        time.sleep(0.3)
    return z


# ─── R1: GA_EV sparse-enum membership ───────────────────────────────

@pytest.mark.parametrize("ev", [1, 3, 5, 10, 11])
def test_R1_gaev_rejects_gaps_and_internal_events(raw_plc_socket, ev):
    """E_RobotEvent is sparse (1/3/5 are tombstoned gaps) and 10/11 are
    internal (EV_OK / EV_ER). The 1..11 range check lets all of these
    through with ack=TRUE while the FSM no-ops (or worse, EV_OK skips a
    hardware handshake). Contract: host-postable events are exactly
    {2,4,6,7,8,9}; anything else NAKs. (memory: plc_event_numeric_values)"""
    s = raw_plc_socket
    # Park in UnInited so an accidentally-accepted event has the least
    # authority (no Powering handshake to skip).
    _send_recv(s, {"type": "SYS", "cmd": "GA_EV", "ev": EV_RESET}, 81000 + ev)
    time.sleep(0.3)
    r = _send_recv(s, {"type": "SYS", "cmd": "GA_EV", "ev": ev}, 81100 + ev)
    assert r is not None, "GA_EV ev=%d silently dropped" % ev
    assert r.get("ack") is False, (
        "GA_EV ev=%d ACKed (sparse gap / internal event accepted); "
        "expected NAK. reply=%r" % (ev, r))
    # Leave FSM clean regardless.
    _send_recv(s, {"type": "SYS", "cmd": "GA_EV", "ev": EV_RESET}, 81200 + ev)


def test_R1b_gaev_ev_none_is_noop_state_poll(raw_plc_socket):
    """EV_NONE (ev=0) must be a valid NO-OP state poll: ack:true, current
    state returned, FSM unchanged. The renderer's init_plc_motion loop
    sends GA_EV(0) to read st_str before driving the power-up sequence;
    the R1 membership tightening NAK'd it and PluginHello rejects any
    ack:false, killing the loop on iteration 1. Regression guard."""
    s = raw_plc_socket
    _send_recv(s, {"type": "SYS", "cmd": "GA_EV", "ev": EV_RESET}, 81500)
    time.sleep(0.3)
    before = _send_recv(s, {"type": "SYS", "cmd": "GET_MACHINE_STATE"}, 81501)
    r = _send_recv(s, {"type": "SYS", "cmd": "GA_EV", "ev": 0}, 81502)
    assert r is not None, "GA_EV ev=0 silently dropped"
    assert r.get("ack") is True, (
        "GA_EV ev=0 (EV_NONE state poll) NAK'd -- breaks init_plc_motion. "
        "reply=%r" % r)
    assert r.get("st_str"), "GA_EV ev=0 reply carried no st_str: %r" % r
    after = _send_recv(s, {"type": "SYS", "cmd": "GET_MACHINE_STATE"}, 81503)
    assert (before or {}).get("st") == (after or {}).get("st"), (
        "GA_EV ev=0 changed FSM state (%s -> %s); must be a pure no-op"
        % ((before or {}).get("st"), (after or {}).get("st")))


# ─── R2: unknown motion cmd NAK must carry err ──────────────────────

def test_R2_unknown_motion_cmd_nak_has_err(virtual_motors_forced, raw_plc_socket):
    s = raw_plc_socket
    assert bring_fsm_to_ready(s, base_id=82000)
    r = _send_recv(s, {"type": "M", "cmd": "NO_SUCH_CMD"}, 82600)
    assert r is not None, "unknown M cmd silently dropped"
    assert r.get("ack") is False
    assert isinstance(r.get("err"), str) and r.get("err"), (
        "bare NAK without err string: %r" % r)


# ─── R3/R4: M4 registration-gate rejection must NAK ─────────────────

def test_R3_m4_coord1_bind_wrong_trig_naks(virtual_motors_forced, raw_plc_socket):
    """coord1_bind MUST pair with trig=130 (PulseTrigger) + positive
    exit_pulse_offset. A violating M4 is currently consumed with no
    reply and no counter (ProcessMotionPacket gate has no ELSE)."""
    s = raw_plc_socket
    assert bring_fsm_to_ready(s, base_id=83000)
    r = _send_recv(s, {
        "type": "M", "cmd": "M4", "action": "coord1_bind",
        "trig": 20, "exit_pulse_offset": 100,
    }, 83600)
    assert r is not None, (
        "M4 coord1_bind with trig=20 silently dropped (no NAK, no counter) "
        "-- no_silent_drops violation")
    assert r.get("ack") is False
    assert r.get("err"), "NAK without err string: %r" % r


def test_R4_m4_pin_op_zero_stages_naks(virtual_motors_forced, raw_plc_socket):
    """pin_op M4 whose pin_op_seq nets zero stages (all masks 0) fails
    the registration gate -> currently silent."""
    s = raw_plc_socket
    assert bring_fsm_to_ready(s, base_id=84000)
    r = _send_recv(s, {
        "type": "M", "cmd": "M4", "motion_id": 1,
        "pin_op_seq": [0, 0, 0],   # mask 0 -> stage discarded -> count 0
    }, 84600)
    assert r is not None, (
        "M4 pin_op with zero net stages silently dropped -- "
        "no_silent_drops violation")
    assert r.get("ack") is False
    assert r.get("err"), "NAK without err string: %r" % r


# ─── R5: WAIT_FOR_TRIGGER refusal must NAK ──────────────────────────

def test_R5_wait_for_trigger_refusal_naks(virtual_motors_forced, raw_plc_socket):
    """Explicit motion_id=0 fails the registration gate; the packet is
    currently consumed with ShouldReturn never set -> host times out.
    (The gate also wrongly reads scratch MotionOutputPin -- removing
    that term is part of the fix; this test only pins the NAK contract.)"""
    s = raw_plc_socket
    assert bring_fsm_to_ready(s, base_id=85000)
    r = _send_recv(s, {
        "type": "M", "cmd": "WAIT_FOR_TRIGGER_MOTION_PROGRESS",
        "motion_id": 0, "motion_progress": 0.5, "ttl_ms": 1000,
    }, 85600)
    assert r is not None, (
        "WAIT_FOR_TRIGGER_MOTION_PROGRESS refusal silently dropped -- "
        "no_silent_drops violation")
    assert r.get("ack") is False
    assert r.get("err"), "NAK without err string: %r" % r


# ─── R6: id-less G1 pair must both execute ──────────────────────────

def test_R6_idless_g1s_both_execute(virtual_motors_forced, raw_plc_socket):
    """Missing 'id' parses to -1 but the dedupe ring's no-id sentinel is
    0, so the second id-less G1 false-matches the first's cache entry
    and the motion is silently skipped. Contract: id-less packets are
    never dedupe candidates."""
    s = raw_plc_socket
    _ready_with_coord(s, 86000)
    # Targets must sit inside the delta workspace (XY +-25, Z -130..-70;
    # out-of-workspace G1s ACK but silently don't move -- memory:
    # delta_workspace_z_negative). Same envelope the virtual soak uses.
    # Completion is detected by polling arm_z to the target: buffer_size
    # drops to 0 while the last move is still executing, so it's not a
    # position-settled signal.
    z_a, z_b = -80.0, -100.0
    send_pack(s, {"type": "M", "cmd": "G1", "X": 0, "Y": 0, "Z": z_a, "F": 200})
    z1 = _wait_arm_z(s, z_a, 86700)
    assert z1 is not None and abs(z1 - z_a) < 0.5, (
        "G1 #1 didn't reach Z=%s (arm_z=%r) -- environment issue" % (z_a, z1))
    # G1 #2 (no id) -> Z=-100.
    send_pack(s, {"type": "M", "cmd": "G1", "X": 0, "Y": 0, "Z": z_b, "F": 200})
    z2 = _wait_arm_z(s, z_b, 86850)
    assert z2 is not None and abs(z2 - z_b) < 0.5, (
        "second id-less G1 was dedupe-skipped (arm_z=%r, expected %s) -- "
        "id sentinel -1 cached and false-matched" % (z2, z_b))


# ─── R7: SCRATCHPAD_WRITE range-NAK ─────────────────────────────────

def test_R7_scratchpad_write_range_naks(raw_plc_socket):
    """Values beyond the DINT wire width must NAK (same spirit as the
    partial_scratchpad NAK), not silently wrap into a different number
    that reads back wrong."""
    s = raw_plc_socket
    # Baseline write so we know the pre-state.
    base = {"plan_id": 77, "plan_index": 3, "intent_kind": 1,
            "intent_movement_id": 42, "last_vision_pulse": 0}
    ok = _send_recv(s, dict(base, type="SYS", cmd="SCRATCHPAD_WRITE"), 87000)
    assert ok and ok.get("ack") is True, "baseline scratchpad write failed"
    # Oversized intent_movement_id: 2^32 + 5 wraps to 5 via TO_UDINT.
    r = _send_recv(s, dict(base, type="SYS", cmd="SCRATCHPAD_WRITE",
                           intent_movement_id=2**32 + 5), 87001)
    assert r is not None, "oversized SCRATCHPAD_WRITE silently dropped"
    assert r.get("ack") is False, (
        "oversized intent_movement_id (2^32+5) ACKed -- silent wrap. "
        "reply=%r" % r)
    st = _machine_state(s, 87002)
    got = (st or {}).get("scratchpad", {}).get("intent_movement_id")
    assert got == 42, (
        "scratchpad stomped by rejected write: intent_movement_id=%r "
        "(expected baseline 42)" % got)


# ─── R8: RESET_DBG_INFO must clear every published counter ──────────

# GET_DIAG keys that are counters (canonical-list contract: SYS
# RESET_DBG_INFO zeroes ALL of these). Non-counter keys deliberately
# excluded: runtime_ms, sm_scans, server_active, bind_addr,
# last_ui_ping_ms, flyevent_avail. ui_ping_count is re-bumped by our own
# keepalive within ms of the reset, so it gets a small tolerance.
DIAG_COUNTER_KEYS = [
    "remp_overflow_drop", "remp_drop", "overlen_drop", "send_stall_drop",
    "group_not_ready_nak", "missing_type_nak", "coord_not_cfg_nak",
    "proto_mismatch_nak", "idle_reset", "read_err_reset",
    "parser_err_reset", "write_err_reset", "client_connect_count",
    "server_long_idle_count", "ui_hb_stale_count",
    "group_error_stop_trips", "ping_max_gap_ms", "st_chg_event_count",
    "self_reentry", "pending_stchg_drop", "pending_movedone_drop",
    "remp_drop_reply", "remp_drop_trig", "dupe_cmd",
    "io_cmd_count", "io_trig_count",
    # Added by the R2/R3/R4/R5/R11 fixes:
    "unknown_cmd_nak", "flyevent_reject_nak", "flyevent_full_nak",
]


def test_R8_reset_dbg_clears_all_published_counters(virtual_motors_forced,
                                                    raw_plc_socket):
    """Two RESET_DBG_INFO handlers have drifted; neither clears the newer
    counters. Contract: one canonical list = everything GET_DIAG
    publishes as a counter. We deterministically dirty a both-lists-miss
    counter (dupe_cmd via a duplicate-id G1) before resetting."""
    s = raw_plc_socket
    _ready_with_coord(s, 88000)
    # Dirty dupe_cmd: same id twice. Second reply is the dedupe echo.
    pkt = {"type": "M", "cmd": "G1", "X": 0, "Y": 0, "Z": -5.0, "F": 200,
           "id": 88600}
    send_pack(s, pkt)
    assert drain_until_id(s, 88600, 3.0) is not None
    send_pack(s, pkt)
    dup = drain_until_id(s, 88600, 3.0)
    assert dup is not None, "dedupe echo missing"
    pre = _get_diag(s, 88700)
    assert pre is not None and pre.get("dupe_cmd", 0) >= 1, (
        "test setup failed to bump dupe_cmd: %r" % pre)
    # Reset via the SYS path (the canonical one).
    rst = _send_recv(s, {"type": "SYS", "cmd": "RESET_DBG_INFO"}, 88800)
    assert rst is not None and rst.get("ack") is True
    diag = _get_diag(s, 88900)
    assert diag is not None
    stale = {k: diag.get(k) for k in DIAG_COUNTER_KEYS
             if diag.get(k) not in (0, None)}
    assert not stale, (
        "RESET_DBG_INFO left published counters non-zero (drifted reset "
        "list): %r" % stale)
    # Keys the PLC doesn't publish yet are their own (soft) finding --
    # surface them so the canonical list stays honest.
    missing = [k for k in DIAG_COUNTER_KEYS if k not in diag]
    assert not missing, "GET_DIAG no longer publishes: %r" % missing
    assert diag.get("ui_ping_count", 0) <= 5, (
        "ui_ping_count=%r right after reset (keepalive is 1Hz; >5 means "
        "the reset didn't clear it)" % diag.get("ui_ping_count"))


# ─── R9: no duplicate map keys in NAK replies ───────────────────────

def _recv_raw_frame_for_id(s, expect_id, timeout=NAK_WAIT_S):
    """Collect raw bytes and return the exact msgpack frame whose decoded
    object carries expect_id (so we can inspect wire-level key layout)."""
    buf = b""
    deadline = time.time() + timeout
    s.settimeout(0.3)
    while time.time() < deadline:
        try:
            chunk = s.recv(4096)
        except OSError:
            continue
        if not chunk:
            break
        buf += chunk
        unp = msgpack.Unpacker(raw=False, strict_map_key=False)
        unp.feed(buf)
        start = 0
        try:
            for obj in unp:
                end = unp.tell()
                if isinstance(obj, dict) and obj.get("id") == expect_id:
                    return buf[start:end]
                start = end
        except Exception:
            pass
    return None


def test_R9_nak_reply_has_no_duplicate_keys(raw_plc_socket):
    """GA_EV/SCRATCHPAD/COORD1_BIND NAK branches pack ack:FALSE and the
    commit helper appends id+ack again -> duplicate 'ack' key in one map.
    Decoders take the last value today; the contract is one key, once."""
    s = raw_plc_socket
    send_pack(s, {"type": "SYS", "cmd": "GA_EV", "ev": 0, "id": 89600})
    frame = _recv_raw_frame_for_id(s, 89600)
    assert frame is not None, "no NAK reply frame captured"
    n_ack = frame.count(b"\xa3ack")
    assert n_ack == 1, (
        "reply frame carries 'ack' key %d times (duplicate map key): %r"
        % (n_ack, frame))


# ─── R10: abort must not fabricate MOVE_DONE / completion ───────────

def test_R10_abort_does_not_emit_move_done(virtual_motors_forced,
                                           raw_plc_socket):
    """EV_ERROR mid-motion aborts the queue; the MOVE_DONE edge detector
    (buffer >0 -> 0) has no error gate, so it currently fires for the
    aborted move AND advances last_completed_movement_id -- poisoning
    the §4(2) resume reconcile into 'completed' (double reel advance).
    Contract: aborted moves emit no MOVE_DONE; last_completed stays at
    the last genuinely finished move."""
    s = raw_plc_socket
    _ready_with_coord(s, 90000)
    drain_all(s, 0.3)   # flush stale events
    # Queue a chain of moves slow enough to observe in-flight state.
    last_acked_mid = None
    # In-workspace targets (Z -130..-70) so the moves genuinely execute.
    for i, z in enumerate([-80.0, -95.0, -110.0, -125.0]):
        r = _send_recv(s, {"type": "M", "cmd": "G1",
                           "X": 0, "Y": 0, "Z": z, "F": 10.0}, 90600 + i)
        assert r and r.get("ack") is True, "G1 %d not acked: %r" % (i, r)
        if r.get("movement_id"):
            last_acked_mid = r["movement_id"]
    assert last_acked_mid, "G1 acks carried no movement_id"
    st = _machine_state(s, 90700)
    if not st or st.get("motion_buffer_size", 0) == 0:
        pytest.skip("virtual axes drained the whole chain before we could "
                    "abort -- rerun on real axes / slower F")
    # Abort mid-flight.
    ev = _send_recv(s, {"type": "SYS", "cmd": "GA_EV", "ev": EV_ERROR}, 90710)
    assert ev and ev.get("ack") is True
    events = drain_all(s, 2.0)
    move_dones = [o for o in events if isinstance(o, dict)
                  and o.get("name") == "MOVE_DONE"]
    fabricated = [o for o in move_dones
                  if o.get("movement_id") == last_acked_mid]
    post = _machine_state(s, 90720)
    try:
        assert not fabricated, (
            "MOVE_DONE emitted for aborted move %s: %r"
            % (last_acked_mid, fabricated))
        assert post is not None
        assert post.get("last_completed_movement_id", 0) < last_acked_mid, (
            "last_completed_movement_id=%r advanced to/past the aborted "
            "move %s -- resume would classify state-2 as completed"
            % (post.get("last_completed_movement_id"), last_acked_mid))
    finally:
        _send_recv(s, {"type": "SYS", "cmd": "GA_EV", "ev": EV_RESET}, 90730)
        time.sleep(0.4)


# ─── R11: FlyEvent saturation must NAK, not wedge the queue ─────────

def test_R11_flyevent_saturation_naks_instead_of_wedging(
        virtual_motors_forced, raw_plc_socket):
    """With <=3 free FlyEvent slots, an M4 currently sits unconsumed at
    the minfo tail (no ELSE on the avail gate) and the dispatcher EXITs
    on a type-M tail -- every packet behind it (including SYS) stalls,
    with zero counters. Contract: NAK err='flyevent_buffer_full' and
    keep the queue draining.

    Self-healing: filler events carry ttl_ms=12s and a never-matching
    distance trigger, so even on red the wedge clears itself via TTL."""
    s = raw_plc_socket
    assert bring_fsm_to_ready(s, base_id=91000)
    diag = _get_diag(s, 91500)
    avail0 = (diag or {}).get("flyevent_avail")
    if avail0 is None or avail0 < 10:
        pytest.skip("FlyEvent slots dirty (avail=%r); need a clean 10" % avail0)
    filler = {
        "type": "M", "cmd": "M4", "motion_id": 1, "trig": 120,
        "tx": 1500.0, "ty": 1500.0, "tz": 1500.0, "td": 0.5, "tin": 1,
        "ttl_ms": 12000, "pin_op_seq": [0, 0x4000, 0],
    }
    fills = 0
    for i in range(7):
        r = _send_recv(s, dict(filler), 91600 + i)
        if not (r and r.get("ack")):
            break
        fills += 1
        if (r.get("ev_buf_space") or 99) <= 4:
            break
    assert fills >= 1, "could not register any filler fly events"
    # Next M4: with avail<=3 this is the wedge packet pre-fix, or an
    # immediate NAK post-fix.
    send_pack(s, dict(filler, id=91700))
    wedge_reply = drain_until_id(s, 91700, NAK_WAIT_S)
    # THE core assertion: the queue behind the M4 must keep moving.
    probe = _machine_state(s, 91710)
    try:
        assert probe is not None, (
            "GET_MACHINE_STATE stalled behind a saturated-slot M4 -- "
            "whole inbound queue wedged, invisible to every counter")
        assert wedge_reply is not None, (
            "saturated-slot M4 got no reply (silent drop)")
        assert wedge_reply.get("ack") is False
        assert wedge_reply.get("err"), (
            "expected err (e.g. flyevent_buffer_full): %r" % wedge_reply)
    finally:
        # Let TTLs expire so the next test starts clean (and, pre-fix,
        # so the wedge itself clears).
        deadline = time.time() + 16.0
        while time.time() < deadline:
            d = _get_diag(s, 91800)
            if d and d.get("flyevent_avail", 0) >= 10:
                break
            time.sleep(1.0)
