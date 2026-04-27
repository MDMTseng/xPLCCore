# Recovery-path regression. The happy path lives in test_movement_e2e.py;
# this file exercises the most common production fault sequence:
#   Ready -> EV_ERROR -> Error -> EV_RESET -> UnInited -> re-power/enable
#   /home -> Ready -> SetCoord0 -> G1.
#
# The coord-system gate clears on the UnInited entry, so a regression that
# breaks SetCoord re-application would surface here as a coord_not_configured
# NAK on the post-recovery G1 -- which is the same failure operators report
# after estop/reset cycles.
import pytest

import test_movement_sequence as tms

UNINITED = 10
ERROR = 990
EV_ERROR = 9


def test_error_reset_back_to_ready(fsm_ready):
    """Drive Ready -> Error -> Reset -> Ready and verify a G1 still works."""
    # Trip into Error
    tms.fire_event(EV_ERROR, "EV_ERROR", 0.6)
    st = tms.wait_for_state(ERROR, timeout=4.0, label="Error")
    assert st == ERROR, f"FSM did not enter Error (last st={st})"

    # Reset clears the latched error and drops us through UnInited.
    tms.fire_event(tms.EV_RESET, "EV_RESET", 0.6)
    st = tms.wait_for_state(UNINITED, timeout=4.0, label="UnInited")
    assert st == UNINITED, f"FSM did not return to UnInited (last st={st})"

    # The UnInited entry clears GVL.CoordSystemConfigured. Confirm the
    # coord_set probe matches: a polled GET_MACHINE_STATE must report
    # coord_set=False so the UI banner / push event is consistent.
    snap = tms.get_state()
    assert snap.get("coord_set") is False, (
        f"coord gate not cleared on UnInited entry: {snap}"
    )

    # Re-walk the boot sequence
    tms.fire_event(tms.EV_POWER_ON, "EV_POWER_ON", 1.0)
    tms.fire_event(tms.EV_GROUP_ENABLE, "EV_GROUP_ENABLE", 1.0)
    tms.fire_event(tms.EV_HOME_GO_FORCE_SKIP, "EV_HOME_GO_FORCE_SKIP", 0.6)
    st = tms.wait_for_state(tms.READY, timeout=8.0, label="Ready")
    assert st == tms.READY, f"FSM did not re-reach Ready post-recovery (last st={st})"


def test_g1_nak_without_setcoord_after_recovery(fsm_ready):
    """A G1 issued after recovery, before SetCoord, must NAK
    'coord_not_configured' -- not silently move."""
    # Assume previous test left us in Ready post-recovery; if it didn't run,
    # drive there explicitly without re-running SetCoord.
    snap = tms.get_state()
    if snap.get("st") != tms.READY:
        tms.fire_event(tms.EV_RESET, "EV_RESET", 0.4)
        tms.fire_event(tms.EV_POWER_ON, "EV_POWER_ON", 1.0)
        tms.fire_event(tms.EV_GROUP_ENABLE, "EV_GROUP_ENABLE", 1.0)
        tms.fire_event(tms.EV_HOME_GO_FORCE_SKIP, "EV_HOME_GO_FORCE_SKIP", 0.6)
        tms.wait_for_state(tms.READY, timeout=8.0, label="Ready")

    snap = tms.get_state()
    assert snap.get("coord_set") is False, (
        f"coord gate was already set post-recovery -- did SetCoord0 leak across? {snap}"
    )

    g1 = tms.harness_send(
        {"type": "M", "cmd": "G1", "X": 0.0, "Y": 0.0, "Z": 0.0},
        timeout=3.0,
    )
    # The harness wraps PLC NAKs as `ok: False, err: <plc_err>` (the inner
    # `ack: False` from the PLC reply is folded into `ok`). Match on err
    # alone so a future harness-shape change doesn't masquerade as a PLC bug.
    assert g1.get("err") == "coord_not_configured", (
        f"expected err='coord_not_configured', got {g1!r}"
    )


def test_g1_works_after_recovery_and_setcoord(fsm_ready):
    """SetCoord0 must re-open the gate, and G1 must complete."""
    snap = tms.get_state()
    if snap.get("st") != tms.READY:
        pytest.skip("FSM not in Ready -- earlier recovery test must have failed")

    sc = tms.harness_send({"type": "M", "cmd": "SetCoord0"}, timeout=3.0)
    assert sc.get("ack"), f"SetCoord0 did not ack post-recovery: {sc}"

    snap = tms.get_state()
    assert snap.get("coord_set") is True, (
        f"coord gate not set after SetCoord0: {snap}"
    )

    mid = tms.do_move("post_recovery", 0.0, 0.0, 0.0)
    assert mid is not None, "G1 did not complete post-recovery"
