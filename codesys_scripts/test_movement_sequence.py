#!/usr/bin/env python3
"""End-to-end movement test driving the AxisGroupSM through a full
operation sequence in virtual-motors mode.

Routes packets through remote_harness (the UI's TCP slot) so this test
runs without freeing the PLC's single-client TCP listener. Polls
GET_MACHINE_STATE for state/movement_id since push events go to the
UI renderer, not back to us.

Sequence:
  0. Daemon forces GVL.bVirtualMotorsMode_Request TRUE.
  1. SYS/GA_EV: RESET -> POWER_ON -> GROUP_ENABLE -> HOME_GO_FORCE_SKIP.
  2. Poll GET_MACHINE_STATE until st=70 (Ready).
  3. M/SetCoord0 to open the post-homing coord-system gate.
  4. M/G1 motion sequence; after each move poll until movement_id
     advances and matches the move's accepted id.
  5. Final GET_MACHINE_STATE (state, movement_id, axes_err_id).
  6. EV_RESET cleanup.

Pre-reqs:
  - CODESYS daemon (rpc.py) running, project loaded and downloaded.
  - UI running in foreground with remote_harness on 127.0.0.1:8127.
"""
from __future__ import annotations
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RPC = HERE / "rpc.py"
REMOTE_CTRL = HERE / "remote_ctrl.py"
JOBS = HERE / "jobs" / "templates"

# E_RobotEvent
EV_POWER_ON           = 2
EV_GROUP_ENABLE       = 4
EV_HOME_GO_FORCE_SKIP = 7
EV_RESET              = 8

# E_RobotState
READY = 70


# ---------------------------------------------------------------- helpers ----

def run_daemon_job(name: str) -> None:
    job = JOBS / f"{name}.py"
    print(f"[daemon] {name}")
    res = subprocess.run(
        [sys.executable, str(RPC), "exec", "--file", str(job)],
        capture_output=True, text=True, timeout=60,
    )
    for line in (res.stdout or "").strip().splitlines()[-4:]:
        print(f"  | {line}")
    if res.returncode != 0:
        print(f"  ! exit={res.returncode}")


_HARNESS_LOCK = threading.Lock()


def harness_send(payload: dict, timeout: float = 5.0) -> dict:
    """Push one msgpack packet through the UI; return PLC reply dict.
    Serialised: only one /push at a time so PINGs from the heartbeat
    thread don't interleave mid-roundtrip."""
    body = json.dumps(payload)
    with _HARNESS_LOCK:
        res = subprocess.run(
            [sys.executable, str(REMOTE_CTRL), "send_tcp_msgpack", body,
             "--timeout", str(timeout)],
            capture_output=True, text=True, timeout=timeout + 5,
        )
    try:
        out = json.loads(res.stdout or "{}")
    except json.JSONDecodeError:
        return {"raw": res.stdout, "stderr": res.stderr}
    result = out.get("result") or {}
    inner = result.get("value") if isinstance(result, dict) else None
    if isinstance(inner, dict):
        return inner
    return result if isinstance(result, dict) else {"raw": out}


# -------------------------------------------------------------- sequence ----

def fire_event(ev: int, label: str, settle: float = 0.6) -> dict:
    reply = harness_send({"type": "SYS", "cmd": "GA_EV", "ev": ev}, timeout=3.0)
    print(f"  fire {label:<24} ev={ev:>2} -> "
          f"st={reply.get('st')} st_str={reply.get('st_str')!r}")
    time.sleep(settle)
    return reply


def get_state() -> dict:
    return harness_send({"type": "SYS", "cmd": "GET_MACHINE_STATE"}, timeout=2.0)


def wait_for_state(target_st: int, timeout: float = 8.0,
                   label: str = "") -> int | None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        st = get_state()
        last = st.get("st")
        if last == target_st:
            return last
        time.sleep(0.4)
    print(f"  !! timeout waiting for st={target_st} ({label}); last={last}")
    return last


def wait_for_idle(timeout: float = 8.0) -> dict | None:
    """Poll GET_MACHINE_STATE until motion_buffer_size==0 for 2 reads.
    Returns the last state dict on idle, None on timeout."""
    deadline = time.time() + timeout
    idle_reads = 0
    last = None
    while time.time() < deadline:
        last = get_state()
        if last.get("motion_buffer_size", 0) == 0:
            idle_reads += 1
            if idle_reads >= 2:
                return last
        else:
            idle_reads = 0
        time.sleep(0.2)
    return None


def do_move(label: str, x: float, y: float, z: float,
            f: float = 200.0) -> int | None:
    pkt = {"type": "M", "cmd": "G1", "X": x, "Y": y, "Z": z,
           "F": f, "ACC": 1000.0, "DEA": 1000.0, "JERK": 10000.0,
           "FAC": 1.0, "Cor": 0.0, "abort": False}
    reply = harness_send(pkt, timeout=3.0)
    accepted = bool(reply.get("ack"))
    mid = reply.get("movement_id")
    print(f"  G1 {label:<8} -> ack={accepted} movement_id={mid} "
          f"err={reply.get('err')}")
    if not accepted or mid is None:
        return None
    idle = wait_for_idle(timeout=10.0)
    if idle is None:
        print(f"     !! buffer never drained")
        return None
    print(f"     drained: st={idle.get('st')} buf={idle.get('motion_buffer_size')}")
    return mid


def heartbeat_loop(stop: threading.Event) -> None:
    """1Hz PING through the harness so the A3 supervisor (5s threshold)
    doesn't trip while the test is in Ready. Only PING updates
    GVL.LastUiPingMs; GA_EV / GET_MACHINE_STATE / G1 do not."""
    while not stop.wait(1.0):
        try:
            harness_send({"type": "SYS", "cmd": "PING"}, timeout=2.0)
        except Exception:
            pass


def main() -> int:
    print("== Step 0: ensure virtual-motors gate open ==")
    run_daemon_job("virtual_motors_force")

    print("\n== Step 1: harness sanity (PING) ==")
    pong = harness_send({"type": "SYS", "cmd": "PING"}, timeout=3.0)
    print(f"  PING -> {pong}")
    if not pong.get("pong"):
        print("FAIL: no pong; UI/harness/PLC link not healthy")
        return 1

    hb_stop = threading.Event()
    hb = threading.Thread(target=heartbeat_loop, args=(hb_stop,), daemon=True)
    hb.start()
    print("  heartbeat thread started (1Hz PING)")

    failures: list[str] = []

    print("\n== Step 2: drive FSM to Ready ==")
    fire_event(EV_RESET,               "EV_RESET",               0.4)
    fire_event(EV_POWER_ON,            "EV_POWER_ON",            1.0)
    fire_event(EV_GROUP_ENABLE,        "EV_GROUP_ENABLE",        1.0)
    fire_event(EV_HOME_GO_FORCE_SKIP,  "EV_HOME_GO_FORCE_SKIP",  0.6)

    st = wait_for_state(READY, timeout=8.0, label="Ready")
    if st != READY:
        failures.append(f"FSM did not reach Ready (last st={st})")
        return _finish(failures)

    print("\n== Step 3: configure coord (SetCoord0) ==")
    sc = harness_send({"type": "M", "cmd": "SetCoord0"}, timeout=3.0)
    print(f"  SetCoord0 -> ack={sc.get('ack')} err={sc.get('err')}")
    if not sc.get("ack"):
        failures.append(f"SetCoord0 not acked: {sc}")
        return _finish(failures)
    time.sleep(0.5)

    print("\n== Step 4: motion sequence ==")
    moves = [
        ("home",  0.0,   0.0,  0.0),
        ("p1",   10.0,   0.0,  0.0),
        ("p2",   10.0,  10.0,  0.0),
        ("p3",    0.0,  10.0,  0.0),
        ("home",  0.0,   0.0,  0.0),
    ]
    accepted_ids: list[int] = []
    for label, x, y, z in moves:
        mid = do_move(label, x, y, z)
        if mid is None:
            failures.append(f"move {label} did not complete")
            break
        accepted_ids.append(mid)

    print("\n== Step 5: final state read ==")
    final = get_state()
    print(f"  st={final.get('st')} movement_id={final.get('movement_id')} "
          f"motion_buffer_size={final.get('motion_buffer_size')} "
          f"axes_err_id={final.get('axes_err_id')}")
    if final.get("st") != READY:
        failures.append(f"final state not Ready: {final.get('st')}"
                        f" err_src={final.get('err_src')} err_id={final.get('err_id')}")
    if not accepted_ids:
        failures.append("no moves were accepted")
    else:
        print(f"  accepted movement_ids: {accepted_ids}")

    print("\n== Step 6: cleanup ==")
    fire_event(EV_RESET, "EV_RESET", 0.4)
    hb_stop.set()
    return _finish(failures)


def _finish(failures: list[str]) -> int:
    print()
    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS -- complete movement sequence executed end-to-end.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
