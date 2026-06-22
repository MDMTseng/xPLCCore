# pytest fixtures for end-to-end movement regression. Reuses helpers from
# test_movement_sequence.py (the non-pytest harness driver) so the actual
# transport / heartbeat / sequence logic lives in one place.
#
# Run with:
#   pytest codesys_scripts/tests/ -v
#
# Pre-reqs (same as test_movement_sequence.py):
#   - CODESYS daemon (rpc.py) running, project loaded and downloaded
#   - UI running in foreground with remote_harness on 127.0.0.1:8127
import sys
import threading
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PARENT = HERE.parent
sys.path.insert(0, str(PARENT))
# test_movement_sequence.py was moved to _archive/ in the script-tree
# cleanup (commit 41c8241) but conftest still consumes its helpers.
# Add the archive dir to sys.path so the import keeps working without
# resurrecting the file at top level.
sys.path.insert(0, str(PARENT / "_archive"))

import test_movement_sequence as tms  # noqa: E402
# tms.HERE/RPC/JOBS are relative to the archived test_movement_sequence.py
# location (_archive/...) which doesn't host these files anymore. Repoint
# to the live codesys_scripts/ root.
tms.HERE = PARENT
tms.RPC = PARENT / "rpc.py"
tms.JOBS = PARENT / "jobs" / "templates"
tms.REMOTE_CTRL = PARENT / "internals" / "remote_ctrl.py"


@pytest.fixture(scope="session")
def virtual_motors_forced():
    tms.run_daemon_job("virtual_motors_force")
    yield
    tms.run_daemon_job("virtual_motors_unforce")


@pytest.fixture(scope="session")
def heartbeat(virtual_motors_forced):
    pong = tms.harness_send({"type": "SYS", "cmd": "PING"}, timeout=3.0)
    if not pong.get("pong"):
        pytest.skip("UI/harness/PLC link not healthy (no pong)")
    stop = threading.Event()
    th = threading.Thread(target=tms.heartbeat_loop, args=(stop,), daemon=True)
    th.start()
    yield
    stop.set()
    th.join(timeout=2.0)


@pytest.fixture(scope="session")
def fsm_ready(heartbeat):
    tms.fire_event(tms.EV_RESET, "EV_RESET", 0.4)
    tms.fire_event(tms.EV_POWER_ON, "EV_POWER_ON", 1.0)
    tms.fire_event(tms.EV_GROUP_ENABLE, "EV_GROUP_ENABLE", 1.0)
    tms.fire_event(tms.EV_HOME_GO_FORCE_SKIP, "EV_HOME_GO_FORCE_SKIP", 0.6)
    st = tms.wait_for_state(tms.READY, timeout=8.0, label="Ready")
    assert st == tms.READY, f"FSM did not reach Ready (last st={st})"
    sc = tms.harness_send({"type": "M", "cmd": "SetCoord0"}, timeout=3.0)
    assert sc.get("ack"), f"SetCoord0 not acked: {sc}"
    yield
    tms.fire_event(tms.EV_RESET, "EV_RESET", 0.4)
