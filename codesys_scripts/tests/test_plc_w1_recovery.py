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

def test_T3_heartbeat_lost_trips_to_error(virtual_motors_forced):
    """No-keepalive socket. Drive to Ready. Stop pinging. After
    HB_TIMEOUT_MS + margin, FSM must be in Error."""
    ui_set_tcp(False); time.sleep(0.4)
    s = _open_socket()
    try:
        assert bring_fsm_to_ready(s), "could not reach Ready"
        # Sanity: we're in Ready right now.
        cur = _machine_state(s, 73001)
        assert cur and cur.get("st") == ST_READY
        # Stop talking. Sleep > HB_TIMEOUT_MS. Don't send PING during.
        time.sleep(HB_OBSERVE_S)
        # Probe state. This single packet itself stamps LastUiPingMs only
        # if it's a PING -- GET_MACHINE_STATE doesn't. So state should
        # already be Error from the supervisor having fired during the
        # silent window.
        post = _machine_state(s, 73002)
        assert post is not None, "no reply after silent window"
        assert post.get("st") == ST_ERROR, (
            "supervisor did not trip Ready -> Error after %.1fs silence "
            "(observed st=%s)" % (HB_OBSERVE_S, post.get("st")))
    finally:
        s.close(); time.sleep(0.3); ui_set_tcp(True)


# ─── T4 ──────────────────────────────────────────────────────────────

def test_T4_post_trip_snapshot_includes_error_source(virtual_motors_forced):
    """After the heartbeat trip, GET_MACHINE_STATE must carry a populated
    err_src so a fresh renderer can show *why* the machine is in Error."""
    ui_set_tcp(False); time.sleep(0.4)
    s = _open_socket()
    try:
        assert bring_fsm_to_ready(s)
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

def test_T5_reset_after_trip_recovers(virtual_motors_forced):
    """EV_RESET after a heartbeat trip must drive FSM to UnInited (so the
    renderer can drive forward to Ready again)."""
    ui_set_tcp(False); time.sleep(0.4)
    s = _open_socket()
    try:
        assert bring_fsm_to_ready(s)
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
