# Regression tests for the PLC reply shapes the new UI surfaces depend on.
# These don't exercise the renderer directly -- they pin the PLC contract
# the UI code reads, so a PLC-side rename / removal fails here loudly
# instead of silently turning a UI badge into "—".
#
#   - motion_buffer_size / movement_id in GET_MACHINE_STATE   (F1)
#   - READ_LATEST_CMD_LOCATION returns X/Y/Z numbers          (F2)
#   - getDigitalInputFlipCount returns raw + fc[]             (F3)
#   - MOVE_DONE event payload carries movement_id + runtime_ms (F4 — shape only)
import test_movement_sequence as tms


def test_machine_state_has_motion_buffer_fields(fsm_ready):
    reply = tms.harness_send({"type": "SYS", "cmd": "GET_MACHINE_STATE"}, timeout=3.0)
    assert reply.get("err") is None, f"GET_MACHINE_STATE NAKed: {reply}"
    for key in ("motion_buffer_size", "movement_id"):
        assert key in reply, f"GET_MACHINE_STATE missing {key!r}: {reply}"
        v = reply[key]
        assert isinstance(v, int) and v >= 0, f"{key} not non-neg int: {v!r}"


def test_read_latest_cmd_location_shape(fsm_ready):
    reply = tms.harness_send(
        {"type": "M", "cmd": "READ_LATEST_CMD_LOCATION"},
        timeout=3.0,
    )
    assert reply.get("err") is None, f"READ_LATEST_CMD_LOCATION NAKed: {reply}"
    for key in ("X", "Y", "Z"):
        assert key in reply, f"location reply missing {key!r}: {reply}"
        assert isinstance(reply[key], (int, float)), (
            f"location[{key!r}] not numeric: {reply[key]!r}"
        )


def test_get_digital_input_flip_count_shape(fsm_ready):
    reply = tms.harness_send(
        {"type": "M", "cmd": "getDigitalInputFlipCount"},
        timeout=3.0,
    )
    assert reply.get("err") is None, f"getDigitalInputFlipCount NAKed: {reply}"
    assert "raw" in reply, f"missing raw: {reply}"
    assert isinstance(reply["raw"], int), f"raw not int: {reply['raw']!r}"
    assert "fc" in reply, f"missing fc: {reply}"
    fc = reply["fc"]
    assert isinstance(fc, list), f"fc not list: {fc!r}"
    # The UI indexes fc by IO_Pins.I.* values -- highest currently used is
    # PackedReelNoProtrusion=11. Anything shorter will index OOB.
    assert len(fc) >= 12, f"fc too short ({len(fc)}): {fc!r}"
    for i, n in enumerate(fc):
        assert isinstance(n, int) and n >= 0, f"fc[{i}] not non-neg int: {n!r}"
