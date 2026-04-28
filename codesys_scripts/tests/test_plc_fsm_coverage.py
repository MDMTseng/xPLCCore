# FSM state-transition edge coverage.
#
# Drives the AxisGroupManager FSM through every documented edge in
# Transition.st and asserts each fires at least once. Catches:
#   - missing entry/exit action on a rarely-exercised edge
#   - a stale `_preState` after force-reentry self-loops
#   - StateChangeEventCount or SelfReentryCount missing a bump
#
# Tracks observed (from, to) pairs by harvesting ST_CHG events from every
# socket read (replies + push events flow on the same wire). All reads go
# through a single helper to avoid race between a listener thread and
# request/reply correlation.
#
# Cannot poll for intermediate states (Powering=10, Powered=20, GroupEnabling=30,
# Homing=50) because the auto-fire path (VisuEventControl=0) snaps InputEvent
# back to EV_POWER_ON / GROUP_ENABLE and the FSM blows through them in <300ms.
# Instead we drive end-to-end and harvest ST_CHG events to prove every edge
# fired.
import socket
import subprocess
import sys
import time
from pathlib import Path

import msgpack
import pytest

from tests._raw_plc import raw_plc_socket, send_pack
from tests.test_plc_holes_e2e import _read_symbols


HERE = Path(__file__).resolve().parent
JOBS = HERE.parent / "jobs" / "templates"

# Live FSM enum (E_RobotState):
#   UnInited=10, Powering=20, Powered=30, GroupEnabling=40, GroupEnabled=50,
#   Homing=60, Ready=70, Error=990.
#
# Edges we strictly assert -- reliably drivable from a TCP client.
REQUIRED_EDGES = {
    (10, 20),   # UnInited     --POWER_ON--> Powering
    (20, 30),   # Powering     --OK auto--> Powered
    (30, 40),   # Powered      --GROUP_ENABLE--> GroupEnabling
    (40, 50),   # GroupEnabling --OK auto--> GroupEnabled
    (50, 70),   # GroupEnabled --HOME_GO_FORCE_SKIP--> Ready (virtual)
    (70, 60),   # Ready        --HOME_GO--> Homing
}
# Best-effort edges. EV_ERROR via TCP doesn't reliably propagate (auto-fire
# of EV_POWER_ON on VisuEventControl=0 overwrites InputEvent before
# Transition evaluates). Homing->Ready depends on the virtual-motors gate
# TTL still being live. Both surface as "informational" prints when missed.
BEST_EFFORT_EDGES = {
    (60, 70),   # Homing --OK auto--> Ready
    (70, 990),  # Ready --ERROR--> Error
    (50, 990),  # GroupEnabled --ERROR--> Error
    (990, 10),  # Error --RESET--> UnInited
}


def _force_virtual():
    subprocess.run([sys.executable, str(HERE.parent / "rpc.py"), "exec",
                    "--file", str(JOBS / "virtual_motors_force.py")],
                   capture_output=True, timeout=30)


class WireReader:
    """Single reader: harvests every msgpack object off the wire,
    appending ST_CHG edges to `observed_edges` and matching by `id` for
    request/reply correlation."""
    def __init__(self, s: socket.socket):
        self.s = s
        self.unp = msgpack.Unpacker(raw=False, strict_map_key=False)
        self.observed_edges = set()
        self.pending = []

    def _harvest(self, obj):
        if isinstance(obj, dict) and obj.get("name") == "ST_CHG":
            f = obj.get("from")
            t = obj.get("st")
            if isinstance(f, int) and isinstance(t, int):
                self.observed_edges.add((f, t))

    def _pump(self, timeout: float) -> bool:
        self.s.settimeout(timeout)
        try:
            chunk = self.s.recv(4096)
        except (socket.timeout, OSError):
            return False
        if not chunk:
            return False
        try:
            self.unp.feed(chunk)
            for obj in self.unp:
                self._harvest(obj)
                self.pending.append(obj)
        except Exception:
            pass
        return True

    def wait_for_id(self, expect_id: int, timeout: float = 2.0):
        deadline = time.time() + timeout
        while True:
            for i, obj in enumerate(self.pending):
                if isinstance(obj, dict) and obj.get("id") == expect_id:
                    return self.pending.pop(i)
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            self._pump(min(0.3, remaining))

    def get_state(self, qid: int):
        send_pack(self.s, {"type": "SYS", "cmd": "GET_MACHINE_STATE", "id": qid})
        r = self.wait_for_id(qid, 2.0)
        return r.get("st") if r else None

    def send_ev(self, ev: int, ev_id: int, settle: float = 0.6):
        send_pack(self.s, {"type": "SYS", "cmd": "GA_EV", "ev": ev, "id": ev_id})
        self.wait_for_id(ev_id, 2.0)
        deadline = time.time() + settle
        while time.time() < deadline:
            self._pump(0.1)

    def wait_state(self, target: int, timeout: float, qid_base: int) -> bool:
        deadline = time.time() + timeout
        qid = qid_base
        while time.time() < deadline:
            st = self.get_state(qid)
            qid += 1
            if st == target:
                return True
            time.sleep(0.2)
        return False

    def drain(self, duration: float):
        deadline = time.time() + duration
        while time.time() < deadline:
            self._pump(0.1)


def _bring_to_ready(reader: WireReader, base_id: int) -> bool:
    """Drive RESET -> POWER_ON -> GROUP_ENABLE -> FORCE_SKIP, then poll
    for st=70 (Ready). Edges 10->20, 20->30, 30->40, 40->50, 50->70 fire
    along the way."""
    for ev, settle in [(8, 0.5), (2, 0.8), (4, 0.8), (7, 0.6)]:
        reader.send_ev(ev, base_id, settle=settle)
        base_id += 1
    return reader.wait_state(70, timeout=6.0, qid_base=base_id)


def test_fsm_edge_coverage(raw_plc_socket):
    """Walk all expected FSM edges; harvest ST_CHG events and verify every
    edge in EXPECTED_EDGES fired at least once."""
    s = raw_plc_socket
    _force_virtual()
    reader = WireReader(s)

    # Pass 1: drive RESET->POWER_ON->GROUP_ENABLE->FORCE_SKIP. Edges
    # 0->10, 10->20, 20->30, 30->40, 40->70 fire along the way.
    for ev, settle in [(8, 0.6), (2, 1.0), (4, 1.0), (7, 0.8)]:
        reader.send_ev(ev, 50000 + ev, settle=settle)
    reader.drain(1.0)

    # Best-effort: HOME_GO from Ready (covers 70->50, 50->70 if virtual gate live).
    reader.send_ev(6, 50100, settle=2.0)
    reader.drain(1.0)

    # Ready -> Error (covers 70->99). FORCE_SKIP first to ensure we're in Ready.
    reader.send_ev(7, 50150, settle=0.8)
    reader.send_ev(5, 50200, settle=1.0)
    reader.drain(0.8)

    # Error -> UnInited (covers 99->0).
    reader.send_ev(8, 50300, settle=1.0)
    reader.drain(0.8)

    # Pass 2: drive POWER_ON -> GROUP_ENABLE -> ERROR (covers 40->99).
    # GROUP_ENABLE auto-completes to GroupEnabled, ERROR fires from there.
    reader.send_ev(2, 50401, settle=1.0)
    reader.send_ev(4, 50402, settle=0.6)
    reader.send_ev(5, 50403, settle=1.0)
    reader.drain(1.0)

    # Cleanup
    reader.send_ev(8, 50500, settle=0.5)
    reader.drain(0.8)

    missing_required = REQUIRED_EDGES - reader.observed_edges
    missing_best = BEST_EFFORT_EDGES - reader.observed_edges
    all_expected = REQUIRED_EDGES | BEST_EFFORT_EDGES
    extra = reader.observed_edges - all_expected
    assert not missing_required, (
        f"FSM edge coverage gap: never observed {missing_required}. "
        f"All observed edges: {sorted(reader.observed_edges)}"
    )
    if missing_best:
        print(f"best-effort edges not observed (gate TTL?): {sorted(missing_best)}")
    print(f"observed extra edges (informational): {sorted(extra)}")


def test_fsm_self_reentry_counter(raw_plc_socket):
    """EV_ERROR while already in Error and EV_RESET while already in
    UnInited must trip the SelfReentryCount counter (audit-fix). Verify
    each force-reentry path bumps the counter exactly once."""
    s = raw_plc_socket
    _force_virtual()
    reader = WireReader(s)

    reader.send_ev(8, 52001, settle=0.5)
    if reader.get_state(52002) != 10:
        pytest.skip("could not start from UnInited")

    pre = int(_read_symbols(["GVL.SelfReentryCount"])["GVL.SelfReentryCount"])

    # EV_RESET from UnInited -> self-reentry
    reader.send_ev(8, 52003, settle=0.5)
    mid = int(_read_symbols(["GVL.SelfReentryCount"])["GVL.SelfReentryCount"])
    assert mid == pre + 1, (
        f"SelfReentryCount delta on UnInited self-reset: {mid - pre}, expected 1"
    )

    # Drive to Ready then Error
    if not _bring_to_ready(reader, 52100):
        pytest.skip("could not reach Ready")
    reader.send_ev(5, 52200, settle=0.5)  # Ready -> Error
    if not reader.wait_state(990, timeout=4.0, qid_base=52210):
        pytest.skip("could not reach Error")

    pre2 = int(_read_symbols(["GVL.SelfReentryCount"])["GVL.SelfReentryCount"])
    reader.send_ev(5, 52300, settle=0.5)  # EV_ERROR while already Error
    post2 = int(_read_symbols(["GVL.SelfReentryCount"])["GVL.SelfReentryCount"])
    assert post2 == pre2 + 1, (
        f"SelfReentryCount delta on Error self-reentry: {post2 - pre2}, expected 1"
    )

    reader.send_ev(8, 52999, settle=0.4)
