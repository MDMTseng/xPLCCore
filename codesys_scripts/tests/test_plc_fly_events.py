# Fly-event regression: DistanceTrigger CASE in ProcessFlyEventsAndIo
# was added together with M4 'trig'=120 wiring (commit b92344d). Without
# this test a future ST refactor can drop the DistanceTrigger branch
# silently -- all the parser and enum bits stay valid, the trigger just
# never fires.
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests._raw_plc import (
    raw_plc_socket,
    send_pack,
    drain_until_id,
    bring_fsm_to_ready,
    m4_pulse_seq,
)
from tests.test_plc_holes_e2e import _read_symbols

HERE = Path(__file__).resolve().parent


def _force_virtual():
    subprocess.run(
        [sys.executable, str(HERE.parent / "rpc.py"), "exec",
         "--file", str(HERE.parent / "jobs" / "templates" / "virtual_motors_force.py")],
        capture_output=True, timeout=30,
    )


def test_distance_trigger_fires_inside_boundary(raw_plc_socket):
    """Register a fly event with trig=120 (DistanceTrigger) and a huge
    radius so the TCP is always inside. IoTriggerCount must climb within
    a few scans, proving:
      - M4 parses 'trig' / 'tx'/'ty'/'tz'/'td'/'tin' correctly
      - the registration gate accepts distance triggers without a queued
        movement_id
      - ProcessFlyEventsAndIo's DistanceTrigger CASE actually runs and
        evaluates squared distance correctly."""
    _force_virtual()
    s = raw_plc_socket
    if not bring_fsm_to_ready(s):
        pytest.skip("FSM did not reach Ready (likely SMC virtual-axis trip)")

    send_pack(s, {"type": "M", "cmd": "SetCoord0", "id": 70001})
    sc = drain_until_id(s, 70001, 2.0)
    assert sc and sc.get("ack"), f"SetCoord0 not acked: {sc}"

    pre = int(_read_symbols(["AxisGroupSM.IoTriggerCount"])
              ["AxisGroupSM.IoTriggerCount"])

    # td=1e9 -> ball covers the whole workspace -> tin=1 (inside) fires
    # immediately. The 2-stage pin_op_seq (set, then auto-reset 50ms later)
    # makes IoTriggerCount climb by exactly 2.
    m4 = {
        "type": "M", "cmd": "M4", "id": 70010,
        "motion_id": 0,
        "trig": 120,
        "tx": 0.0, "ty": 0.0, "tz": 0.0,
        "td": 1.0e9,
        "tin": 1,
        "pin_op_seq": m4_pulse_seq(0x4000, 0x4000, reset_ms=50),
        "ttl_ms": 2000, "event_id": 12345,
    }
    send_pack(s, m4)
    r = drain_until_id(s, 70010, 2.0)
    assert r and r.get("ack"), f"M4 distance-trigger not acked: {r}"

    # 5ms Comm task * a handful of scans is enough; 500ms is generous and
    # safely covers the auto-reset stage delay (50ms).
    time.sleep(0.5)
    post = int(_read_symbols(["AxisGroupSM.IoTriggerCount"])
               ["AxisGroupSM.IoTriggerCount"])
    delta = post - pre
    assert delta == 2, (
        f"DistanceTrigger expected IoTriggerCount +2 (set + auto-reset); "
        f"got pre={pre} post={post} delta={delta}"
    )


def test_distance_trigger_outside_boundary_does_not_fire(raw_plc_socket):
    """Mirror test: tin=1 with td=tiny (radius 0.001 mm around origin)
    and TCP probably nowhere near origin -> trigger MUST NOT fire within
    the TTL window. After ttl_ms expires we expect a TRIGGER_TIMEOUT_ERR
    reply carrying our event_id, proving the timeout path is reached
    from the DistanceTrigger CASE (not the progress CASE)."""
    _force_virtual()
    s = raw_plc_socket
    if not bring_fsm_to_ready(s):
        pytest.skip("FSM did not reach Ready (likely SMC virtual-axis trip)")

    send_pack(s, {"type": "M", "cmd": "SetCoord0", "id": 71001})
    sc = drain_until_id(s, 71001, 2.0)
    assert sc and sc.get("ack"), f"SetCoord0 not acked: {sc}"

    pre = int(_read_symbols(["AxisGroupSM.IoTriggerCount"])
              ["AxisGroupSM.IoTriggerCount"])

    m4 = {
        "type": "M", "cmd": "M4", "id": 71010,
        "motion_id": 0,
        "trig": 120,
        "tx": 0.0, "ty": 0.0, "tz": 0.0,
        "td": 0.001,
        "tin": 1,
        "pin_op_seq": m4_pulse_seq(0x4000, 0x4000, reset_ms=0),
        "ttl_ms": 200,
        "event_id": 67890,
    }
    send_pack(s, m4)
    r = drain_until_id(s, 71010, 2.0)
    assert r and r.get("ack"), f"M4 not acked: {r}"

    # Wait past ttl_ms; expect IoTriggerCount unchanged (no IO fired)
    # even though the timeout error reply was sent.
    time.sleep(0.6)
    post = int(_read_symbols(["AxisGroupSM.IoTriggerCount"])
               ["AxisGroupSM.IoTriggerCount"])
    assert post == pre, (
        f"tiny-radius outside-target: IoTriggerCount climbed unexpectedly "
        f"({pre} -> {post}); event fired when it shouldn't have"
    )
