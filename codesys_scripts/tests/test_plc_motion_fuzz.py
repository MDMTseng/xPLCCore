# G1 motion parameter fuzz.
#
# Brings FSM to Ready + SetCoord0, then hammers the G1 dispatch path with
# pathological motion parameters: NaN/Inf coords, huge magnitudes, missing
# axis fields, conflicting CommandIds, wrap-around movement_id collisions.
#
# Invariants:
#   - PLC must NAK or accept each G1, never crash / never wedge
#   - FSM must remain in Ready throughout (no spurious EV_ERROR)
#   - StateChangeEventCount stays flat (no transitions out of Ready)
#   - DupeCommandCount climbs only on legitimate duplicate CommandIds
#   - On nominal G1 surrounded by junk, movement_id still advances
import math
import random
import time

import pytest

from tests._raw_plc import (
    raw_plc_socket, send_pack, drain_until_id, bring_fsm_to_ready,
)
from tests.test_plc_holes_e2e import _read_symbols


@pytest.fixture
def fsm_ready_raw(raw_plc_socket):
    s = raw_plc_socket
    # Force virtual motors via daemon job (fast inline call).
    import subprocess, sys
    from pathlib import Path
    HERE = Path(__file__).resolve().parent
    JOBS = HERE.parent / "jobs" / "templates"
    subprocess.run([sys.executable, str(HERE.parent / "rpc.py"), "exec",
                    "--file", str(JOBS / "virtual_motors_force.py")],
                   capture_output=True, timeout=30)
    if not bring_fsm_to_ready(s):
        pytest.skip("could not bring FSM to Ready")
    send_pack(s, {"type": "M", "cmd": "SetCoord0", "id": 91500})
    sc = drain_until_id(s, 91500, 2.0)
    if not (sc and sc.get("ack")):
        pytest.skip(f"SetCoord0 not acked: {sc}")
    yield s


def _nominal_g1(cid: int, x=0.0, y=0.0, z=0.0):
    return {"type": "M", "cmd": "G1", "id": cid,
            "X": x, "Y": y, "Z": z, "A": 0,
            "F": 100, "ACC": 1000, "DEA": 1000, "JERK": 10000}


def test_g1_nan_inf_coords_rejected_safely(fsm_ready_raw):
    """NaN/Inf coords must not crash the PLC. They should either NAK
    (preferred) or be clamped/ignored. FSM must stay in Ready."""
    s = fsm_ready_raw
    pre = _read_symbols(["GVL.StateChangeEventCount"])
    pre_state_changes = int(pre.get("GVL.StateChangeEventCount", 0))

    cid = int(time.time_ns() & 0x7FFFFFFF)
    pathological = [
        ("nan-x",   {"X": math.nan}),
        ("inf-x",   {"X": math.inf}),
        ("ninf-x",  {"X": -math.inf}),
        ("nan-y",   {"Y": math.nan}),
        ("nan-z",   {"Z": math.nan}),
        ("nan-all", {"X": math.nan, "Y": math.nan, "Z": math.nan}),
        ("huge-x",  {"X": 1e18}),
        ("huge-f",  {"F": 1e15}),
        ("neg-f",   {"F": -100}),
        ("zero-f",  {"F": 0}),
    ]
    for tag, override in pathological:
        cid += 1
        pkt = _nominal_g1(cid)
        pkt.update(override)
        send_pack(s, pkt)
        reply = drain_until_id(s, cid, 2.0)
        assert reply is not None, f"{tag}: no reply within 2s"
        # Either ack (PLC tolerated/clamped) or NAK with err. Crash/wedge
        # would manifest as None.

    # Verify FSM stayed in Ready.
    send_pack(s, {"type": "SYS", "cmd": "GET_MACHINE_STATE", "id": 91900})
    st = drain_until_id(s, 91900, 2.0)
    assert st and st.get("st") == 70, f"FSM left Ready under NaN/Inf: {st}"

    post = _read_symbols(["GVL.StateChangeEventCount"])
    post_state_changes = int(post.get("GVL.StateChangeEventCount", 0))
    assert post_state_changes == pre_state_changes, (
        f"FSM transitioned {post_state_changes - pre_state_changes} times "
        f"during NaN/Inf fuzz; should have stayed in Ready"
    )


def test_g1_missing_axis_fields_handled(fsm_ready_raw):
    """G1 without X/Y/Z should be NAK'd or treated as zero. Either is
    acceptable; PLC must not crash."""
    s = fsm_ready_raw
    cid = int(time.time_ns() & 0x7FFFFFFF) + 100

    variants = [
        {},                    # absolutely nothing besides type/cmd/id
        {"X": 0},              # only X
        {"F": 100},            # only feed
        {"A": 5},              # only A axis
    ]
    for i, v in enumerate(variants):
        pkt = {"type": "M", "cmd": "G1", "id": cid + i}
        pkt.update(v)
        send_pack(s, pkt)
        reply = drain_until_id(s, cid + i, 2.0)
        assert reply is not None, f"missing-fields variant {v}: no reply"


def test_g1_dedupe_under_collision_burst(fsm_ready_raw):
    """Send 30 G1s rotating through 5 reused CommandIds. Each repeat of a
    CommandId must come back with dedup:True and the same movement_id as
    the original. DupeCommandCount must climb by exactly the repeat count."""
    s = fsm_ready_raw
    pre = _read_symbols(["GVL.DupeCommandCount", "GVL.StateChangeEventCount"])
    pre_dupes = int(pre.get("GVL.DupeCommandCount", 0))
    pre_chgs = int(pre.get("GVL.StateChangeEventCount", 0))

    base = int(time.time_ns() & 0x7FFFFFFF) + 1000
    pool = [base + i for i in range(5)]
    movement_ids = {}
    expected_dupes = 0

    rng = random.Random(0xCAFE)
    for _ in range(30):
        cid = rng.choice(pool)
        send_pack(s, _nominal_g1(cid))
        reply = drain_until_id(s, cid, 2.0)
        assert reply is not None, f"no reply for cid={cid}"

        if cid in movement_ids:
            # repeat -- must dedup and replay cached movement_id
            assert reply.get("dedup") is True, \
                f"repeat cid={cid} not dedup'd: {reply}"
            assert reply.get("movement_id") == movement_ids[cid], \
                f"dedup replay returned wrong movement_id: {reply}"
            expected_dupes += 1
        else:
            assert reply.get("dedup") is None, \
                f"first-time cid={cid} unexpectedly dedup'd: {reply}"
            mvid = reply.get("movement_id")
            assert isinstance(mvid, int) and mvid > 0, \
                f"first-time cid={cid} missing movement_id: {reply}"
            movement_ids[cid] = mvid

    post = _read_symbols(["GVL.DupeCommandCount", "GVL.StateChangeEventCount"])
    post_dupes = int(post.get("GVL.DupeCommandCount", 0))
    post_chgs = int(post.get("GVL.StateChangeEventCount", 0))
    assert post_dupes - pre_dupes == expected_dupes, (
        f"DupeCommandCount delta {post_dupes - pre_dupes}, expected "
        f"{expected_dupes}"
    )
    assert post_chgs == pre_chgs, "FSM transitioned during dedupe burst"


def test_g1_wrong_field_types_nak(fsm_ready_raw):
    """G1 with X as string, F as bool, etc. PLC unpacker must reject
    cleanly without crashing or partially executing the move."""
    s = fsm_ready_raw
    cid = int(time.time_ns() & 0x7FFFFFFF) + 2000

    junk = [
        ("x-string", {"X": "abc"}),
        ("f-bool",   {"F": True}),
        ("acc-list", {"ACC": [1, 2, 3]}),
        ("x-map",    {"X": {"v": 1}}),
        ("id-string",{"id": "not-an-int"}),  # CommandId itself is wrong
    ]
    for tag, override in junk:
        pkt = _nominal_g1(cid)
        pkt.update(override)
        send_pack(s, pkt)
        # Don't strictly need a reply with the right id (id may be wrong).
        # Just verify PLC stays alive: short delay then check via fresh PING.
        time.sleep(0.1)
        cid += 1

    send_pack(s, {"type": "SYS", "cmd": "PING", "id": 92500})
    pong = drain_until_id(s, 92500, 2.0)
    assert pong and pong.get("pong"), \
        f"PLC unresponsive after wrong-type G1 fields: {pong}"


def test_g1_nominal_after_fuzz_still_executes(fsm_ready_raw):
    """After all the junk above, a clean G1 must still execute and
    advance movement_id. Catches a regression where fuzz inputs left
    motion-side state corrupted enough to silently break real moves."""
    s = fsm_ready_raw
    cid = int(time.time_ns() & 0x7FFFFFFF) + 3000

    send_pack(s, _nominal_g1(cid, x=10.0, y=10.0, z=0.0))
    reply = drain_until_id(s, cid, 2.0)
    assert reply is not None and reply.get("ack") is True, \
        f"clean G1 after fuzz failed: {reply}"
    mvid = reply.get("movement_id")
    assert isinstance(mvid, int) and mvid > 0, \
        f"clean G1 missing movement_id: {reply}"
