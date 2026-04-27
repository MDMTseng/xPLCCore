# Dispatcher / parser regression for M4 (fly-event pin trigger) and
# ReelGo (reel motor outside the kinematic group). The happy-path G1
# suite in test_movement_e2e.py covers the hot motion path; these two
# commands run through entirely separate ProcessMotionPacket branches
# (M4 builds a FlyEvent struct; ReelGo dispatches to MoveRelative on
# the reelpullmotor axis), so a regression there silently breaks pin
# triggers / reel moves while G1 still works.
#
# We only verify ACK + reply shape, not physical effect -- the test
# rig runs in virtual-motors mode (no real hardware actuated).
import pytest

import test_movement_sequence as tms


def _expect_ack(reply, label):
    """Harness wraps PLC NAK as ok=False, err=<plc_err>; ACK comes
    through with the inner reply dict (ack: True usually). Treat 'no
    err key + not ok=False' as ACK."""
    err = reply.get("err")
    assert err is None, f"{label}: PLC NAKed with err={err!r}: {reply}"
    # The harness flattens ack=True replies into the inner dict, but
    # the outer harness `ok` field can be False even on PLC ACK if the
    # reply had no `ack` key (e.g. M4's `ev_buf_space` reply). Don't
    # trust `ok`; trust absence of `err`.


def test_reelgo_acks(fsm_ready):
    """ReelGo dispatches to reelpullmotor.MoveRelative. We send a tiny
    distance so virtual mode doesn't have to simulate a long traversal."""
    reply = tms.harness_send(
        {"type": "M", "cmd": "ReelGo",
         "Distance": 1.0, "F": 100.0,
         "ACC": 1000.0, "DEA": 1000.0, "JERK": 10000.0},
        timeout=3.0,
    )
    _expect_ack(reply, "ReelGo")


def _seed_motion():
    """M4 attaches its FlyEvent to the most recently accepted motion --
    if no G1 has run since boot, MotionTargetMovementId resolves to 0 and
    PLC silently drops the M4 (no FlyEvent queued -> ShouldReturn=FALSE
    -> no reply pushed, harness times out). Fire one tiny G1 first so
    LastAcceptedMovementId is non-zero before M4 dispatches."""
    reply = tms.harness_send(
        {"type": "M", "cmd": "G1",
         "X": 0.0, "Y": 0.0, "Z": 0.0,
         "F": 10.0, "ACC": 100.0, "DEA": 100.0, "JERK": 1000.0},
        timeout=3.0,
    )
    assert reply.get("err") is None, f"seed G1 failed: {reply}"


def test_m4_simple_pulse_acks(fsm_ready):
    """M4 simple-pulse form: pin + state + reset_ms. PLC parses pin/state
    into a FlyEvent struct and queues it; reply carries ev_buf_space."""
    _seed_motion()
    reply = tms.harness_send(
        {"type": "M", "cmd": "M4",
         "pin": 1, "state": 1, "reset_ms": 50,
         "motion_id_offset": 0, "motion_progress": 1.0,
         "event_id": 1001, "ttl_ms": 5000},
        timeout=3.0,
    )
    _expect_ack(reply, "M4 simple-pulse")
    # PLC always echoes ev_buf_space on M4 replies, regardless of accept;
    # if it's missing the reply parser drifted.
    assert "ev_buf_space" in reply, (
        f"M4 reply missing ev_buf_space (PLC parser drift?): {reply}"
    )
    assert isinstance(reply["ev_buf_space"], int), reply


def test_m4_multistage_pin_op_seq_acks(fsm_ready):
    """M4 multi-pulse form via pin_op_seq: array of [delay, pin, state]
    triplets. Exercises the array-unpacker code path, separate from
    simple-pulse parsing."""
    _seed_motion()
    reply = tms.harness_send(
        {"type": "M", "cmd": "M4",
         "pin_op_seq": [
             0,   1, 1,   # delay=0ms, pin=1, state=1 (turn on)
             100, 1, 0,   # delay=100ms, pin=1, state=0 (turn off)
         ],
         "motion_id_offset": 0, "motion_progress": 1.0,
         "event_id": 2002, "ttl_ms": 5000},
        timeout=3.0,
    )
    _expect_ack(reply, "M4 pin_op_seq")
    assert "ev_buf_space" in reply, reply


def test_dispatcher_unchanged_after_motion_branches(fsm_ready):
    """After exercising M4 + ReelGo, the FSM must still be Ready and
    the SYS path must still work. Catches a regression where one of
    those branches inadvertently latches an error or wedges the
    dispatcher."""
    snap = tms.get_state()
    assert snap.get("st") == tms.READY, (
        f"FSM no longer Ready after M4/ReelGo dispatch: {snap}"
    )
    # SYS/PING through the same dispatcher must still pong.
    pong = tms.harness_send({"type": "SYS", "cmd": "PING"}, timeout=3.0)
    assert pong.get("pong") is True, f"PING broken post-dispatch: {pong}"
