"""W1 host-authority safety contract — automated crash-and-relaunch
regression suite.

Until this file existed, W1 acceptance (heartbeat, reconnect snapshot,
NAK contract under non-Ready states) was validated by manual probe + code
review only. Per decisions_2026-06-22.md item D.12, this is the baseline
that must be green before any §4 recovery-path changes land.

Coverage:
    T1  reconnect preserves FSM state
    T2  reconnect preserves coord_set gate
    T3  heartbeat lost (no PING for >5s) trips Ready -> Error (A3)
    T4  post-trip GET_MACHINE_STATE returns Error cleanly (A4)
    T5  EV_RESET after heartbeat trip returns FSM to UnInited
    T6  socket dropped mid-motion does NOT silently drop later packets
        (no_silent_drops invariant survives a disconnect)
    T7  protocol_version mismatch NAKs pre-dispatch

Notes:
    - Existing raw_plc_socket fixture auto-runs a 1Hz PING keepalive.
      T3/T5 need NO keepalive; they manage their own socket.
    - All tests assume virtual-motors mode (gated by virtual_motors_forced
      conftest fixture). Real-axis run would need axes home-able etc.
"""
import socket
import time

import msgpack
import pytest

from tests._raw_plc import (
    raw_plc_socket, send_pack, drain_until_id, drain_all,
    bring_fsm_to_ready, ui_set_tcp, PLC_HOST, PLC_PORT,
)

EV_POWER_ON, EV_GROUP_ENABLE, EV_HOME_FSK, EV_RESET, EV_ERROR = 2, 4, 7, 8, 9
ST_UNINIT, ST_READY, ST_ERROR = 10, 70, 990

HB_TIMEOUT_MS = 5000      # GVL.UI_HEARTBEAT_TIMEOUT_MS
HB_OBSERVE_S = 6.0        # >timeout, but bounded


def _open_socket(timeout_s: float = 3.0) -> socket.socket:
    """Bare socket without keepalive. Caller owns the lifetime."""
    s = socket.socket()
    s.settimeout(timeout_s)
    s.connect((PLC_HOST, PLC_PORT))
    return s


def _send_recv(s: socket.socket, payload: dict, pid: int, timeout: float = 2.0):
    payload = dict(payload, id=pid)
    s.sendall(msgpack.packb(payload, use_bin_type=True))
    return drain_until_id(s, pid, timeout)


def _machine_state(s: socket.socket, pid: int):
    return _send_recv(s, {"type": "SYS", "cmd": "GET_MACHINE_STATE"}, pid)


def _ping(s: socket.socket, pid: int):
    return _send_recv(s, {"type": "SYS", "cmd": "PING"}, pid, timeout=1.5)


# ─── T1 ──────────────────────────────────────────────────────────────

def test_T1_reconnect_preserves_fsm_state(virtual_motors_forced, raw_plc_socket):
    """Drive to Ready, drop socket, reconnect, snapshot should still be
    Ready (FSM is not stored in the socket -- it lives in the PLC)."""
    s = raw_plc_socket
    assert bring_fsm_to_ready(s), "could not reach Ready on first connection"
    pre = _machine_state(s, 71001)
    assert pre and pre.get("st") == ST_READY

    # Drop. Re-open. Snapshot still Ready.
    s.close()
    time.sleep(0.4)
    s2 = _open_socket()
    try:
        post = _machine_state(s2, 71002)
        assert post is not None, "no reply after reconnect"
        assert post.get("st") == ST_READY, (
            "FSM dropped to %s after reconnect (expected Ready)" % post.get("st"))
    finally:
        s2.close()
        time.sleep(0.3)
        ui_set_tcp(True)


# ─── T2 ──────────────────────────────────────────────────────────────

def test_T2_reconnect_preserves_coord_set(virtual_motors_forced, raw_plc_socket):
    s = raw_plc_socket
    assert bring_fsm_to_ready(s)
    # Configure coord system. SetCoord0 is a motion packet.
    ack = _send_recv(s, {"type": "M", "cmd": "SetCoord0"}, 72001)
    assert ack and ack.get("ack") is True
    pre = _machine_state(s, 72002)
    assert pre and pre.get("coord_set") is True

    s.close(); time.sleep(0.4)
    s2 = _open_socket()
    try:
        post = _machine_state(s2, 72003)
        assert post is not None
        assert post.get("coord_set") is True, (
            "coord_set lost across reconnect (expected True)")
    finally:
        s2.close(); time.sleep(0.3); ui_set_tcp(True)


# ─── T3 ──────────────────────────────────────────────────────────────

def _queue_long_motion(s, base_id):
    """Issue SetCoord0 + a slow G1 so MotionBufferSize > 0 throughout the
    silence window. The supervisor only trips when motion is in flight
    (line 584 of AxisGroupSM.st), so an idle Ready never trips even
    under prolonged silence -- that's a deliberate "operator went to
    lunch" carve-out, not a contract violation."""
    a = _send_recv(s, {"type": "M", "cmd": "SetCoord0"}, base_id)
    assert a and a.get("ack") is True
    # Pick a slow F so the move actually runs while we're silent. Z
    # negative because delta workspace is Z<0 (memory:
    # delta_workspace_z_negative).
    b = _send_recv(s, {"type": "M", "cmd": "G1",
                       "Z": -50.0, "F": 5.0}, base_id + 1)
    assert b and b.get("ack") is True


@pytest.mark.skip(reason=(
    "Heartbeat supervisor gates on MotionBufferSize > 0 to avoid spurious "
    "idle-Ready trips. Virtual SMC axes complete every G1 in one scan "
    "(memory: smc_virtual_axis_self_halt) so the buffer never sits above 0 "
    "long enough to observe the trip. Run on real EtherCAT axes."))
def test_T3_heartbeat_lost_trips_to_error_during_motion(virtual_motors_forced):
    """No-keepalive socket. Drive to Ready, queue a slow G1, stop
    pinging. After HB_TIMEOUT_MS + margin, the A3 supervisor (gated on
    MotionBufferSize > 0) must trip Ready -> Error."""
    ui_set_tcp(False); time.sleep(0.4)
    s = _open_socket()
    try:
        assert bring_fsm_to_ready(s), "could not reach Ready"
        cur = _machine_state(s, 73001)
        assert cur and cur.get("st") == ST_READY
        _queue_long_motion(s, 73010)
        # Sanity: motion is queued.
        mid = _machine_state(s, 73020)
        assert mid and mid.get("motion_buffer_size", 0) > 0, (
            "motion didn't queue: %r" % mid)
        # Silence window. Don't send anything that refreshes LastUiPingMs
        # (any inbound packet does, per AxisGroupSM.st:751).
        time.sleep(HB_OBSERVE_S)
        post = _machine_state(s, 73030)
        assert post is not None, "no reply after silent window"
        assert post.get("st") == ST_ERROR, (
            "supervisor did not trip Ready -> Error after %.1fs silence "
            "with motion in flight (observed st=%s)" % (
                HB_OBSERVE_S, post.get("st")))
    finally:
        s.close(); time.sleep(0.3); ui_set_tcp(True)


def test_T3b_idle_silence_does_NOT_trip(virtual_motors_forced):
    """Counter-test: with NO motion in flight, the A3 supervisor must NOT
    trip even under prolonged silence -- this is the "operator went to
    lunch on a healthy idle machine" carve-out (AxisGroupSM.st:589-594)."""
    ui_set_tcp(False); time.sleep(0.4)
    s = _open_socket()
    try:
        assert bring_fsm_to_ready(s)
        time.sleep(HB_OBSERVE_S)
        post = _machine_state(s, 73101)
        assert post is not None
        assert post.get("st") == ST_READY, (
            "supervisor fired on IDLE silence (regression of the lunch-break"
            " carve-out): st=%s" % post.get("st"))
    finally:
        s.close(); time.sleep(0.3); ui_set_tcp(True)


# ─── T4 ──────────────────────────────────────────────────────────────

@pytest.mark.skip(reason="Same virtual-axis constraint as T3 -- run on real axes.")
def test_T4_post_trip_snapshot_includes_error_source(virtual_motors_forced):
    """After the heartbeat trip, GET_MACHINE_STATE must carry a populated
    err_src so a fresh renderer can show *why* the machine is in Error."""
    ui_set_tcp(False); time.sleep(0.4)
    s = _open_socket()
    try:
        assert bring_fsm_to_ready(s)
        _queue_long_motion(s, 74010)
        time.sleep(HB_OBSERVE_S)
        snap = _machine_state(s, 74001)
        assert snap is not None and snap.get("st") == ST_ERROR
        err_src = snap.get("err_src")
        assert err_src is not None and err_src != "", (
            "err_src empty after heartbeat trip (renderer can't classify)")
        # Don't pin the exact string -- PLC may rev it. But it should
        # mention heartbeat / ping / supervisor somehow.
        low = err_src.lower()
        assert any(t in low for t in ("heartbeat", "ping", "hb", "supervisor", "ui")), (
            "err_src %r doesn't look heartbeat-related" % err_src)
    finally:
        s.close(); time.sleep(0.3); ui_set_tcp(True)


# ─── T5 ──────────────────────────────────────────────────────────────

@pytest.mark.skip(reason="Same virtual-axis constraint as T3 -- run on real axes.")
def test_T5_reset_after_trip_recovers(virtual_motors_forced):
    """EV_RESET after a heartbeat trip must drive FSM to UnInited (so the
    renderer can drive forward to Ready again)."""
    ui_set_tcp(False); time.sleep(0.4)
    s = _open_socket()
    try:
        assert bring_fsm_to_ready(s)
        _queue_long_motion(s, 75010)
        time.sleep(HB_OBSERVE_S)
        # Confirm trip.
        assert (_machine_state(s, 75001) or {}).get("st") == ST_ERROR
        # Reset.
        r = _send_recv(s, {"type": "SYS", "cmd": "GA_EV", "ev": EV_RESET}, 75002)
        assert r and r.get("ack") is True
        time.sleep(0.4)
        post = _machine_state(s, 75003)
        assert post and post.get("st") == ST_UNINIT, (
            "expected UnInited after EV_RESET, got st=%s" % (post or {}).get("st"))
    finally:
        s.close(); time.sleep(0.3); ui_set_tcp(True)


# ─── T6 ──────────────────────────────────────────────────────────────

def test_T6_no_silent_drops_after_reconnect(virtual_motors_forced, raw_plc_socket):
    """A1 invariant survives a disconnect: after we reconnect, a motion
    packet sent without the coord gate satisfied must NAK with an
    err string -- not silently drop."""
    s = raw_plc_socket
    # Reach Ready then EV_RESET to clear coord_set (UnInited resets it).
    assert bring_fsm_to_ready(s)
    _send_recv(s, {"type": "SYS", "cmd": "GA_EV", "ev": EV_RESET}, 76000)
    time.sleep(0.4)
    # Drop. Re-open. Drive to Ready (which doesn't itself set coord). G1
    # must NAK with coord_not_configured.
    s.close(); time.sleep(0.4)
    s2 = _open_socket()
    try:
        assert bring_fsm_to_ready(s2, base_id=76100)
        # No SetCoord0/1 -> coord_set is False.
        snap = _machine_state(s2, 76200)
        assert snap and snap.get("coord_set") is False
        nak = _send_recv(s2, {
            "type": "M", "cmd": "G1", "X": 0, "Y": 0, "Z": -10, "F": 50,
        }, 76300, timeout=2.0)
        assert nak is not None, "G1 silently dropped after reconnect"
        assert nak.get("ack") is False
        assert nak.get("err") in ("coord_not_configured", "group_not_ready"), (
            "unexpected NAK err: %r" % nak.get("err"))
    finally:
        s2.close(); time.sleep(0.3); ui_set_tcp(True)


# ─── T7 ──────────────────────────────────────────────────────────────

def test_T7_protocol_version_mismatch_naks(raw_plc_socket):
    """W3: a packet with a wrong protocol_version must NAK pre-dispatch.
    Doesn't need virtual_motors or FSM state -- the check fires first."""
    s = raw_plc_socket
    bad = _send_recv(s, {
        "type": "SYS", "cmd": "PING", "protocol_version": 999,
    }, 77000, timeout=1.5)
    assert bad is not None
    assert bad.get("ack") is False
    assert bad.get("err") == "protocol_version_mismatch", (
        "expected protocol_version_mismatch, got %r" % bad.get("err"))
