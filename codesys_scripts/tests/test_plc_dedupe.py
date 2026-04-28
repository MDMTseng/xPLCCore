# PH#6 G1 command-id dedupe regression.
#
# Why this test uses raw TCP instead of harness_send:
#   The UI's TCP slot rewrites the `id` field with its own monotonic
#   counter, so we can't force the same CommandId across two sends via
#   harness_send. Dedupe is keyed on CommandId echoed back from the
#   client, so we must send raw msgpack with our own `id` to exercise
#   the cache. PLC's TCP listener accepts only one client; the test
#   disconnects the UI for the duration of the probe and reconnects
#   afterward (see ui_plc_single_client_arbitration memory).
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import msgpack
import pytest

from tests.test_plc_holes_e2e import _read_symbols

HERE = Path(__file__).resolve().parent
REMOTE_CTRL = HERE.parent / "remote_ctrl.py"
PLC_HOST, PLC_PORT = "192.168.1.70", 8125


def _ui_set_tcp(connect: bool):
    action = "connect_tcp" if connect else "disconnect_tcp"
    payload = '{"host":"192.168.1.70","port":8125}' if connect else "{}"
    subprocess.run(
        [sys.executable, str(REMOTE_CTRL), action, payload, "--timeout", "3"],
        capture_output=True, text=True, timeout=8,
    )


_send_lock = threading.Lock()


@pytest.fixture
def raw_plc_socket():
    """Steal the PLC's single TCP slot from the UI for this test.
    Reconnect UI on teardown so subsequent fsm_ready-based tests work.
    Also spawn a 1Hz PING keepalive thread -- per the
    plc_event_numeric_values memory, only SYS/PING resets the A3
    UiHeartbeatStale supervisor; GET_MACHINE_STATE doesn't count."""
    _ui_set_tcp(False)
    time.sleep(0.4)
    s = socket.socket(); s.settimeout(3.0)
    s.connect((PLC_HOST, PLC_PORT))

    stop = threading.Event()
    def keepalive():
        i = 0
        while not stop.is_set():
            i += 1
            try:
                with _send_lock:
                    s.sendall(msgpack.packb(
                        {"type": "SYS", "cmd": "PING", "id": 80000 + (i % 1000)},
                        use_bin_type=True,
                    ))
            except OSError:
                return
            stop.wait(0.8)

    th = threading.Thread(target=keepalive, daemon=True)
    th.start()
    try:
        yield s
    finally:
        stop.set()
        th.join(timeout=1.5)
        try: s.close()
        except OSError: pass
        time.sleep(0.3)
        _ui_set_tcp(True)
        time.sleep(0.5)


def _drain_until_id(s: socket.socket, expect_id: int, timeout: float = 2.0):
    """Read msgpack frames off the wire until we see one with id=expect_id.
    Skips push events (HEARTBEAT, COORD_SET, etc.) and stale replies."""
    deadline = time.time() + timeout
    unp = msgpack.Unpacker(raw=False)
    s.settimeout(0.4)
    while time.time() < deadline:
        try:
            chunk = s.recv(4096)
        except socket.timeout:
            continue
        if not chunk:
            return None
        unp.feed(chunk)
        for obj in unp:
            if isinstance(obj, dict) and obj.get("id") == expect_id:
                return obj
    return None


def _send(s: socket.socket, payload: dict):
    with _send_lock:
        s.sendall(msgpack.packb(payload, use_bin_type=True))


def test_g1_dedupe_caches_movement_id(raw_plc_socket):
    """Send two G1s with the same CommandId; the second must come back
    with dedup:True and the cached movement_id, GVL.DupeCommandCount
    must climb. A regression that re-executed the move would surface
    here as the absence of dedup:True or as DupeCommandCount unchanged."""
    s = raw_plc_socket

    # Bring FSM to Ready (FORCE_SKIP homing) and open the coord gate.
    # If FSM is already past UnInited, RESET first.
    _send(s, {"type": "SYS", "cmd": "GA_EV", "ev": 8, "id": 90001})
    assert _drain_until_id(s, 90001, 2.0) is not None
    time.sleep(0.4)
    _send(s, {"type": "SYS", "cmd": "GA_EV", "ev": 2, "id": 90002})
    _drain_until_id(s, 90002, 2.0)
    time.sleep(0.8)
    _send(s, {"type": "SYS", "cmd": "GA_EV", "ev": 4, "id": 90003})
    _drain_until_id(s, 90003, 2.0)
    time.sleep(0.8)
    _send(s, {"type": "SYS", "cmd": "GA_EV", "ev": 7, "id": 90004})
    _drain_until_id(s, 90004, 2.0)
    time.sleep(0.6)

    # Wait for Ready
    for _ in range(20):
        _send(s, {"type": "SYS", "cmd": "GET_MACHINE_STATE", "id": 90005})
        st = _drain_until_id(s, 90005, 2.0)
        if st and st.get("st") == 70:
            break
        time.sleep(0.3)
    assert st and st.get("st") == 70, f"FSM did not reach Ready: {st}"

    _send(s, {"type": "M", "cmd": "SetCoord0", "id": 90006})
    sc = _drain_until_id(s, 90006, 2.0)
    assert sc and sc.get("ack"), f"SetCoord0 not acked: {sc}"

    # Pick a CommandId that's almost certainly absent from the dedupe
    # ring -- using a fixed value would collide with the previous test
    # run since the ring survives pytest sessions (PLC stays online).
    cid = int(time.time_ns() & 0x7FFFFFFF)

    # Read DupeCommandCount baseline.
    pre = int(_read_symbols(["GVL.DupeCommandCount"])["GVL.DupeCommandCount"])

    g1 = {"type": "M", "cmd": "G1", "id": cid,
          "X": 0, "Y": 0, "Z": 0, "A": 0,
          "F": 100, "ACC": 1000, "DEA": 1000, "JERK": 10000}

    # First G1: must be accepted with a real movement_id, no dedup flag.
    _send(s, g1)
    r1 = _drain_until_id(s, cid, 2.0)
    assert r1 is not None, "first G1 got no reply"
    assert r1.get("ack") is True, f"first G1 not acked: {r1}"
    assert r1.get("dedup") is None, f"first G1 unexpectedly dedup'd: {r1}"
    mvid = r1.get("movement_id")
    assert isinstance(mvid, int) and mvid > 0, f"first G1 movement_id missing: {r1}"

    # Second G1, same CommandId: dedup must replay.
    _send(s, g1)
    r2 = _drain_until_id(s, cid, 2.0)
    assert r2 is not None, "second G1 got no reply"
    assert r2.get("dedup") is True, f"second G1 not dedup'd: {r2}"
    assert r2.get("movement_id") == mvid, (
        f"dedup replay returned wrong movement_id "
        f"(got {r2.get('movement_id')}, cached {mvid}): {r2}"
    )
    assert r2.get("ack") is True, f"dedup reply must still ack: {r2}"

    # Counter must have climbed exactly by 1.
    post = int(_read_symbols(["GVL.DupeCommandCount"])["GVL.DupeCommandCount"])
    assert post == pre + 1, (
        f"DupeCommandCount jumped {pre}->{post}, expected exactly +1"
    )

    # Different CommandId, same payload: must NOT dedup.
    g1["id"] = cid + 1
    _send(s, g1)
    r3 = _drain_until_id(s, cid + 1, 2.0)
    assert r3 is not None, "third G1 got no reply"
    assert r3.get("dedup") is None, (
        f"new CommandId got dedup'd -- ring scan is matching too loosely: {r3}"
    )
