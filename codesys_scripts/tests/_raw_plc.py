# Shared raw-TCP helpers for the fuzz/churn/coverage test suites. Lifts
# the duplicate fixture out of test_plc_dedupe / test_plc_fuzz so each
# new test file can drop in via:
#     from tests._raw_plc import raw_plc_socket, send_pack, drain_until_id
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import msgpack
import pytest

HERE = Path(__file__).resolve().parent
REMOTE_CTRL = HERE.parent / "remote_ctrl.py"
PLC_HOST, PLC_PORT = "192.168.1.70", 8125

_send_lock = threading.Lock()


def ui_set_tcp(connect: bool):
    action = "connect_tcp" if connect else "disconnect_tcp"
    payload = '{"host":"192.168.1.70","port":8125}' if connect else "{}"
    subprocess.run(
        [sys.executable, str(REMOTE_CTRL), action, payload, "--timeout", "3"],
        capture_output=True, text=True, timeout=8,
    )


@pytest.fixture
def raw_plc_socket():
    ui_set_tcp(False)
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
                        {"type": "SYS", "cmd": "PING", "id": 60000 + (i % 1000)},
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
        ui_set_tcp(True)
        time.sleep(0.5)


def send_pack(s: socket.socket, payload: dict):
    with _send_lock:
        s.sendall(msgpack.packb(payload, use_bin_type=True))


def drain_until_id(s: socket.socket, expect_id: int, timeout: float = 2.0):
    deadline = time.time() + timeout
    unp = msgpack.Unpacker(raw=False, strict_map_key=False)
    s.settimeout(0.4)
    while time.time() < deadline:
        try:
            chunk = s.recv(4096)
        except socket.timeout:
            continue
        if not chunk:
            return None
        try:
            unp.feed(chunk)
            for obj in unp:
                if isinstance(obj, dict) and obj.get("id") == expect_id:
                    return obj
        except Exception:
            continue
    return None


def drain_all(s: socket.socket, duration: float = 0.3):
    """Read everything available for `duration` seconds. Returns list of
    every msgpack object decoded off the wire."""
    out = []
    unp = msgpack.Unpacker(raw=False, strict_map_key=False)
    deadline = time.time() + duration
    s.settimeout(0.1)
    while time.time() < deadline:
        try:
            chunk = s.recv(4096)
        except (socket.timeout, OSError):
            continue
        if not chunk:
            break
        try:
            unp.feed(chunk)
            for obj in unp:
                out.append(obj)
        except Exception:
            continue
    return out


def bring_fsm_to_ready(s: socket.socket, base_id: int = 91000) -> bool:
    """Drive the FSM through RESET -> POWER_ON -> GROUP_ENABLE ->
    HOME_GO_FORCE_SKIP, then poll GET_MACHINE_STATE until st=70 (Ready).
    Returns True on success. Caller must already have GVL.bVirtualMotorsMode
    forced (typically via the conftest virtual_motors_forced fixture)."""
    for ev, settle in [(8, 0.4), (2, 0.8), (4, 0.8), (7, 0.6)]:
        send_pack(s, {"type": "SYS", "cmd": "GA_EV", "ev": ev, "id": base_id})
        if drain_until_id(s, base_id, 2.0) is None:
            return False
        base_id += 1
        time.sleep(settle)
    for _ in range(20):
        send_pack(s, {"type": "SYS", "cmd": "GET_MACHINE_STATE", "id": base_id})
        st = drain_until_id(s, base_id, 2.0)
        base_id += 1
        if st and st.get("st") == 70:
            return True
        time.sleep(0.3)
    return False
