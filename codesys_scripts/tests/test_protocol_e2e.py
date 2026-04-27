# Protocol-level regression tests for items shipped during the fragility-hunt
# rounds. Each guards a real bug or surface that would otherwise drift back:
#
#   - GET_DIAG payload keys: covers Round 4 #1 (remp_overflow_drop surfaced)
#     and the surrounding diagnostic counters. If any of these go missing the
#     DiagPanel chart breaks silently.
#   - protocol_version gate (W1 A5): mismatched versions must NAK with a
#     specific err string so a half-upgraded UI/PLC pair fails fast instead
#     of running with subtly different payload shapes.
#   - motion_id_offset >32767 (R5 #2): pre-fix this wrapped TO_INT to a
#     negative number and rolled MotionTargetMovementId backwards onto a
#     stale movement. Post-fix: M4 still ACKs and ev_buf_space comes back
#     sane. We can't observe the resolved TargetMovementId from the wire
#     directly, so we settle for "PLC didn't choke and dispatcher is intact."
import pytest

import test_movement_sequence as tms


EXPECTED_DIAG_KEYS = {
    "runtime_ms", "sm_scans",
    "remp_drop", "overlen_drop", "send_stall_drop",
    "remp_overflow_drop",
    "group_not_ready_nak", "missing_type_nak",
    "coord_not_cfg_nak", "proto_mismatch_nak",
    "idle_reset", "read_err_reset", "parser_err_reset",
    "ui_ping_count", "ui_hb_stale_count",
    "ping_max_gap_ms", "last_ui_ping_ms",
    "st_chg_event_count",
}


def test_get_diag_shape(fsm_ready):
    reply = tms.harness_send({"type": "SYS", "cmd": "GET_DIAG"}, timeout=3.0)
    assert reply.get("err") is None, f"GET_DIAG NAKed: {reply}"
    missing = EXPECTED_DIAG_KEYS - set(reply.keys())
    assert not missing, f"GET_DIAG dropped expected keys: {sorted(missing)}; reply={reply}"
    # Every counter must be a non-negative int. A None / string here means
    # the packer code path drifted (wrong PackXxx call for the field type).
    for k in EXPECTED_DIAG_KEYS:
        v = reply[k]
        assert isinstance(v, int), f"GET_DIAG[{k!r}] not int: {v!r}"
        assert v >= 0, f"GET_DIAG[{k!r}] negative: {v}"


def test_protocol_version_legacy_accepted(fsm_ready):
    """Packets with no protocol_version field are legacy / pre-A5 and must
    still ACK (otherwise old harness scripts break on every PLC redeploy)."""
    reply = tms.harness_send({"type": "SYS", "cmd": "PING"}, timeout=3.0)
    assert reply.get("pong") is True, f"legacy PING didn't pong: {reply}"


def test_protocol_version_correct_accepted(fsm_ready):
    reply = tms.harness_send(
        {"type": "SYS", "cmd": "PING", "protocol_version": 1},
        timeout=3.0,
    )
    assert reply.get("pong") is True, f"matching protocol_version PING didn't pong: {reply}"


def test_protocol_version_mismatch_naks(fsm_ready):
    reply = tms.harness_send(
        {"type": "SYS", "cmd": "PING", "protocol_version": 99},
        timeout=3.0,
    )
    assert reply.get("err") == "protocol_version_mismatch", (
        f"expected protocol_version_mismatch NAK, got: {reply}"
    )


def _seed_motion():
    """Same seed-G1 trick used in test_motion_dispatch_e2e -- without a prior
    G1 the PLC's MotionTargetMovementId resolves to 0 and M4 silently drops."""
    reply = tms.harness_send(
        {"type": "M", "cmd": "G1",
         "X": 0.0, "Y": 0.0, "Z": 0.0,
         "F": 10.0, "ACC": 100.0, "DEA": 100.0, "JERK": 1000.0},
        timeout=3.0,
    )
    assert reply.get("err") is None, f"seed G1 failed: {reply}"


@pytest.mark.parametrize("offset", [0, 1, 32768, 100000, -1, -100000])
def test_m4_large_motion_id_offset_acks(fsm_ready, offset):
    """R5 #2 regression: motion_id_offset values outside INT16 range used to
    wrap silently via TO_INT and roll TargetMovementId backwards. Post-fix
    these ACK cleanly. We can't introspect the resolved target id from the
    wire, but a successful ACK + sane ev_buf_space is enough to prove the
    parser path didn't choke."""
    _seed_motion()
    reply = tms.harness_send(
        {"type": "M", "cmd": "M4",
         "pin": 1, "state": 1, "reset_ms": 50,
         "motion_id_offset": offset, "motion_progress": 1.0,
         "event_id": 7000 + (offset & 0xFFFF), "ttl_ms": 5000},
        timeout=3.0,
    )
    # ack=True usually flattens; we trust absence of err (same convention
    # as test_motion_dispatch_e2e).
    assert reply.get("err") is None, (
        f"M4 NAKed with motion_id_offset={offset}: {reply}"
    )
    assert "ev_buf_space" in reply, reply
    assert isinstance(reply["ev_buf_space"], int), reply
