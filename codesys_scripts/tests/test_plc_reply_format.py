# Reply-side wire-format validation.
#
# Existing tests verify the PLC RECEIVES well-formed msgpack. This file
# verifies the PLC SENDS well-formed msgpack. Subscribes to the wire for
# ~10 seconds while exercising the FSM, decodes every emitted object,
# and asserts each follows its event schema.
#
# Catches regressions like:
#   - ele_count mismatch (declared 6 fields but packed 7) -> truncated map
#   - PackString called with NULL -> embedded NUL or buffer overrun
#   - Missing required fields in HEARTBEAT / ST_CHG / MOVE_DONE / COORD_SET
#   - Wrong msgpack type for a field (e.g. movement_id packed as string)
import socket
import time

import msgpack
import pytest

from tests._raw_plc import (
    raw_plc_socket, send_pack, drain_until_id, drain_all,
    bring_fsm_to_ready,
)


# Per-event-name schema. Each entry is { field_name: expected_type_or_tuple }.
# `int` matches any int width; `(int, type(None))` accepts None too.
_EVENT_SCHEMAS = {
    "HEARTBEAT": {
        "kind": str,
        "name": str,
        "runtime_ms": int,
        "remp_drop_count": int,
        "drops_reply": int,
        "drops_events": int,
    },
    "ST_CHG": {
        "kind": str,
        "name": str,
        "st": int,
        "st_str": str,
        "from": int,
        "runtime_ms": int,
    },
    "COORD_SET": {
        "kind": str,
        "name": str,
        "value": bool,
        "runtime_ms": int,
    },
    "MOVE_DONE": {
        "kind": str,
        "name": str,
        "movement_id": int,
        "runtime_ms": int,
    },
}


def _validate_event(obj: dict) -> list:
    """Return a list of validation errors for one decoded object. Empty
    list means the object is OK."""
    errs = []
    if not isinstance(obj, dict):
        return [f"not a dict: {type(obj).__name__}"]
    if obj.get("kind") != "event":
        return []  # not a push event; skip schema check (replies use no `kind`)
    name = obj.get("name")
    schema = _EVENT_SCHEMAS.get(name)
    if schema is None:
        errs.append(f"unknown event name: {name!r}")
        return errs
    for field, expected_type in schema.items():
        if field not in obj:
            errs.append(f"{name}: missing field {field!r}")
            continue
        v = obj[field]
        if not isinstance(v, expected_type):
            errs.append(
                f"{name}.{field}: expected {expected_type}, got "
                f"{type(v).__name__}={v!r}"
            )
    return errs


def test_heartbeat_packets_well_formed(raw_plc_socket):
    """Sit on the wire for 3s. HEARTBEAT fires every 500ms by default,
    so we expect ~6 of them. Each must validate against the schema."""
    s = raw_plc_socket
    objs = drain_all(s, duration=3.0)
    heartbeats = [o for o in objs if isinstance(o, dict) and o.get("name") == "HEARTBEAT"]
    assert len(heartbeats) >= 3, (
        f"expected at least 3 HEARTBEATs in 3s, got {len(heartbeats)}"
    )
    for hb in heartbeats:
        errs = _validate_event(hb)
        assert not errs, f"HEARTBEAT validation failed: {errs} | obj={hb}"


def test_st_chg_packets_well_formed(raw_plc_socket):
    """Drive a few FSM transitions; every emitted ST_CHG must validate."""
    s = raw_plc_socket
    # Force a transition: RESET -> UnInited (or self-loop).
    send_pack(s, {"type": "SYS", "cmd": "GA_EV", "ev": 8, "id": 95001})
    time.sleep(0.6)
    send_pack(s, {"type": "SYS", "cmd": "GA_EV", "ev": 2, "id": 95002})
    time.sleep(1.0)
    send_pack(s, {"type": "SYS", "cmd": "GA_EV", "ev": 8, "id": 95003})
    time.sleep(0.5)

    objs = drain_all(s, duration=1.5)
    st_chgs = [o for o in objs if isinstance(o, dict) and o.get("name") == "ST_CHG"]
    if not st_chgs:
        pytest.skip("no ST_CHG observed (FSM may have been stable already)")
    for evt in st_chgs:
        errs = _validate_event(evt)
        assert not errs, f"ST_CHG validation failed: {errs} | obj={evt}"


def test_coord_set_packet_well_formed(raw_plc_socket):
    """Trigger a COORD_SET edge by sending SetCoord0 from Ready, then
    forcing a reset (which clears the gate, emitting another edge).
    Both edges must produce a well-formed COORD_SET event."""
    s = raw_plc_socket

    # Need to be in Ready for SetCoord0 to take effect.
    import subprocess, sys
    from pathlib import Path
    HERE = Path(__file__).resolve().parent
    JOBS = HERE.parent / "jobs" / "templates"
    subprocess.run([sys.executable, str(HERE.parent / "rpc.py"), "exec",
                    "--file", str(JOBS / "virtual_motors_force.py")],
                   capture_output=True, timeout=30)
    if not bring_fsm_to_ready(s):
        pytest.skip("could not bring FSM to Ready")

    # Drain pre-existing events so we only see ours.
    drain_all(s, duration=0.3)

    send_pack(s, {"type": "M", "cmd": "SetCoord0", "id": 96001})
    drain_until_id(s, 96001, 2.0)
    time.sleep(0.3)

    # Force a reset to clear the gate -> falling COORD_SET edge.
    send_pack(s, {"type": "SYS", "cmd": "GA_EV", "ev": 8, "id": 96002})
    time.sleep(0.5)

    objs = drain_all(s, duration=1.0)
    coord_sets = [o for o in objs if isinstance(o, dict) and o.get("name") == "COORD_SET"]
    if not coord_sets:
        pytest.skip("no COORD_SET observed in window")
    for evt in coord_sets:
        errs = _validate_event(evt)
        assert not errs, f"COORD_SET validation failed: {errs} | obj={evt}"


def test_no_truncated_or_unknown_events(raw_plc_socket):
    """Sit on the wire for 4s and decode every msgpack object. NONE should
    fail to decode (truncation), and every push event with kind=='event'
    must have a known name from the schema."""
    s = raw_plc_socket
    objs = drain_all(s, duration=4.0)
    assert len(objs) > 0, "no traffic at all in 4s -- listener dead?"

    unknown = []
    for o in objs:
        if isinstance(o, dict) and o.get("kind") == "event":
            name = o.get("name")
            if name not in _EVENT_SCHEMAS:
                unknown.append(name)
    assert not unknown, (
        f"PLC emitted events with unknown names (schema drift?): {set(unknown)}"
    )


def test_reply_id_echoes_correctly(raw_plc_socket):
    """For every SYS/PING we send, the reply must echo our `id` exactly
    (not rewritten, not int-truncated, not stringified)."""
    s = raw_plc_socket
    test_ids = [1, 42, 65535, 1 << 30, 2_147_483_000]
    for tid in test_ids:
        send_pack(s, {"type": "SYS", "cmd": "PING", "id": tid})
        reply = drain_until_id(s, tid, 2.0)
        assert reply is not None, f"id={tid} got no echo"
        assert reply.get("id") == tid, \
            f"id mismatch: sent {tid}, got {reply.get('id')!r}"
        assert isinstance(reply.get("id"), int), \
            f"id type changed: {type(reply.get('id'))}"
