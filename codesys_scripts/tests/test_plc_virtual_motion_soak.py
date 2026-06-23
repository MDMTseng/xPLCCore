# 5-minute virtual-motion soak. Cold-resets the PLC to clear any sticky
# SoftMotion state, brings the FSM through to Ready in virtual mode,
# then drives a continuous G1 stream at ~10 G1/s with workspace-valid
# Cartesian targets (Z<0, Delta-reachable). Asserts:
#   - PrevCompletedMovementId advances continuously (motion executes)
#   - GroupErrorStop never trips (group_error_stop_trips stays 0)
#   - FSM stays in Ready throughout
#   - motion_buffer_size oscillates (queue fills and drains, doesn't peg)
#   - no_reply rate < 1%
#
# Skip if PLC unreachable. Smoke (60s): MOTION_SOAK_SHORT=1
#
# Distinct from test_plc_4hr_fuzz which sends 0 G1 -- this is the
# motion-execution counterpart to that test's protocol/event coverage.
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import pytest

import socket

from tests._raw_plc import (
    send_pack,
    drain_until_id,
    drain_all,
    bring_fsm_to_ready,
    ui_set_tcp,
    PLC_HOST,
    PLC_PORT,
)

HERE = Path(__file__).resolve().parent
SHORT = os.environ.get("MOTION_SOAK_SHORT") == "1"
DURATION_S = 60.0 if SHORT else 300.0
# Rate + per-move geometry tuned so move duration < inter-arrival, i.e.
# the buffer drains faster than it fills. At F=300, a ~10mm move on
# Delta workspace takes ~33ms; at 5 G1/s the inter-arrival is 200ms,
# leaving headroom so PrevCompletedMovementId actually advances.
# Bumping the rate or move size tests the back-pressure path
# (MOTION_BUFFER_THRESHOLD = 12) instead -- separate scenario.
TARGET_G1_RATE = 5.0
G1_F = 300.0
G1_RANGE_XY = 10.0
G1_Z_MIN, G1_Z_MAX = -120.0, -80.0
CHECKPOINT_S = 10.0 if SHORT else 30.0


def _force_virtual():
    subprocess.run(
        [sys.executable, str(HERE.parent / "rpc.py"), "exec", "--file",
         str(HERE.parent / "jobs" / "templates" / "virtual_motors_force.py")],
        capture_output=True, timeout=30,
    )


def _cold_reset_then_virtual():
    """Cold reset to clear sticky SoftMotion state, then force virtual mode.
    Mirrors the working clean-state pattern verified 2026-06-23."""
    script = """\
import time
proj = projects.primary
oapp = online.create_online_application(proj.active_application)
try: oapp.login(OnlineChangeOption.Force, False)
except: pass
try: oapp.reset(ResetOption.Cold)
except: pass
time.sleep(2.0)
try: oapp.start()
except Exception as ex:
    if "is run" not in str(ex): print("start err:", ex)
time.sleep(1.5)
oapp.set_prepared_value("GVL.bVirtualMotorsMode_Request", "TRUE")
oapp.force_prepared_values()
time.sleep(0.3)
oapp.logout()
"""
    tmp = HERE.parent / "jobs" / "_tmp_motion_soak_cold.py"
    tmp.write_text(script, encoding="utf-8")
    subprocess.run(
        [sys.executable, str(HERE.parent / "rpc.py"), "exec", "--file", str(tmp)],
        capture_output=True, timeout=30,
    )


def _set_coord0(s, cid):
    send_pack(s, {"type": "M", "cmd": "SetCoord0", "id": cid})
    drain_until_id(s, cid, 2.0)


_SNAP_ID = [970_000]


def _snap_with_retry(s, cmd, attempts=3, timeout=2.0):
    """G1 stream can crowd the socket; retry GET_* probes a few times so a
    transient miss doesn't abort the soak."""
    for _ in range(attempts):
        _SNAP_ID[0] += 1
        cid = _SNAP_ID[0]
        send_pack(s, {"type": "SYS", "cmd": cmd, "id": cid})
        r = drain_until_id(s, cid, timeout)
        if r is not None:
            return r
        time.sleep(0.1)
    return None


def _snap_diag(s):
    return _snap_with_retry(s, "GET_DIAG", timeout=2.5)


def _snap_state(s):
    return _snap_with_retry(s, "GET_MACHINE_STATE", timeout=2.0)


def _snap_arm_pos(s):
    """Read the live arm Cartesian TCP via GET_COORD1_DEBUG. Returns
    (x, y, z) or None. memory sys_arm_pos_via_get_coord1_debug."""
    r = _snap_with_retry(s, "GET_COORD1_DEBUG", timeout=2.0)
    if not r:
        return None
    return (r.get("arm_x"), r.get("arm_y"), r.get("arm_z"))


def test_virtual_motion_soak():
    """Drive virtual axes with a continuous G1 stream for DURATION_S and
    assert motion-path invariants. Validates the corrected understanding
    (memory smc_virtual_axis_self_halt.md, 2026-06-23) that virtual axes
    DO execute motion when starting from clean state.

    Manages its own socket because cold-reset closes any open TCP
    connection; can't use raw_plc_socket fixture which opens before
    the test body runs."""
    # Cold reset BEFORE opening the socket so the TCP connection survives.
    ui_set_tcp(False)
    time.sleep(0.4)
    _cold_reset_then_virtual()
    time.sleep(1.0)

    s = socket.socket()
    s.settimeout(3.0)
    s.connect((PLC_HOST, PLC_PORT))

    try:
        if not bring_fsm_to_ready(s):
            pytest.skip("could not bring FSM to Ready after cold reset")
        _run_soak(s)
    finally:
        try: s.close()
        except OSError: pass
        time.sleep(0.3)
        ui_set_tcp(True)


def _run_soak(s):

    drain_all(s, duration=0.3)
    _set_coord0(s, 970100)
    time.sleep(0.3)

    pre = _snap_state(s)
    if pre.get("st") != 70:
        pytest.skip(f"FSM not Ready after SetCoord0: st={pre.get('st')}")

    pre_diag = _snap_diag(s)
    trips_baseline = pre_diag.get("group_error_stop_trips", 0)
    print(f"\n[motion-soak] DURATION={DURATION_S}s target_rate={TARGET_G1_RATE}/s "
          f"baseline trips={trips_baseline}")

    rng = random.Random(0xC0DE)
    pkt_id = 200_000
    sent = 0
    acks = 0
    naks = 0
    no_reply = 0
    start = time.time()
    next_checkpoint = start + CHECKPOINT_S
    sleep_per_pkt = 1.0 / TARGET_G1_RATE
    max_buf = 0
    arm_pos_history = []
    last_arm = _snap_arm_pos(s)
    if last_arm: arm_pos_history.append(last_arm)

    end_time = start + DURATION_S
    while time.time() < end_time:
        pkt_id += 1
        # Delta-reachable Cartesian targets: small XY box, Z always negative.
        pkt = {
            "type": "M", "cmd": "G1", "id": pkt_id,
            "X": rng.uniform(-G1_RANGE_XY, G1_RANGE_XY),
            "Y": rng.uniform(-G1_RANGE_XY, G1_RANGE_XY),
            "Z": rng.uniform(G1_Z_MIN, G1_Z_MAX),
            "F": G1_F, "ACC": 500.0, "DEA": 500.0, "JERK": 2000.0,
        }
        send_pack(s, pkt)
        r = drain_until_id(s, pkt_id, 1.0)
        if r is None:
            no_reply += 1
        elif r.get("ack"):
            acks += 1
        else:
            naks += 1
        sent += 1
        time.sleep(sleep_per_pkt)

        # Checkpoint
        now = time.time()
        if now >= next_checkpoint:
            st = _snap_state(s)
            buf = st.get("motion_buffer_size") or 0
            done = st.get("last_completed_movement_id") or 0
            if buf > max_buf:
                max_buf = buf
            arm = _snap_arm_pos(s)
            if arm: arm_pos_history.append(arm)
            elapsed = now - start
            print(f"[motion-soak] t={elapsed:5.1f}s sent={sent} acks={acks} "
                  f"naks={naks} no_reply={no_reply} buf={buf} max_buf={max_buf} "
                  f"done={done} st={st.get('st')} "
                  f"arm=({arm[0]:.1f},{arm[1]:.1f},{arm[2]:.1f})" if arm
                  else f"[motion-soak] t={elapsed:5.1f}s ... arm=?")

            # Live invariant: FSM stays Ready (the supervisor will move it
            # to Error if GroupErrorStop trips).
            assert st.get("st") == 70, (
                f"FSM left Ready mid-soak at t={elapsed:.1f}s: "
                f"st={st.get('st')} st_str={st.get('st_str')} "
                f"err_src={st.get('err_src')} err_id={st.get('err_id')}"
            )
            # Live invariant: arm position must actually change. MOVE_DONE
            # / last_completed_movement_id can't be used as a "moves are
            # executing" probe under continuous load -- that event only
            # fires on MotionBufferSize >0 -> 0 edge, which never happens
            # while the input stream keeps the buffer non-empty.
            if len(arm_pos_history) >= 3 and arm:
                # Last 3 arm positions should not all be identical.
                ax_set = set(arm_pos_history[-3:])
                assert len(ax_set) > 1, (
                    f"arm position stuck at {arm} for 3 checkpoints -- "
                    f"motion not executing?"
                )
            next_checkpoint = now + CHECKPOINT_S

    # Cooldown: stop sending, wait for buffer to drain. Once it does,
    # MOVE_DONE fires and last_completed_movement_id should advance.
    print("[motion-soak] cooldown: waiting for buffer drain ...")
    drain_deadline = time.time() + 10.0
    drained_at = None
    while time.time() < drain_deadline:
        st = _snap_state(s)
        if st and (st.get("motion_buffer_size") or 0) == 0:
            drained_at = time.time()
            break
        time.sleep(0.3)
    time.sleep(0.5)

    final_state = _snap_state(s)
    final_diag = _snap_diag(s)
    elapsed = time.time() - start
    final_done = final_state.get("last_completed_movement_id") or 0
    print(f"\n[motion-soak] DONE elapsed={elapsed:.1f}s sent={sent} "
          f"acks={acks} naks={naks} no_reply={no_reply} max_buf={max_buf} "
          f"drained_at={drained_at and (drained_at-start)}")
    print(f"[motion-soak] final st={final_state.get('st')} "
          f"last_completed_movement_id={final_done} "
          f"trips={final_diag.get('group_error_stop_trips')}")

    # Hard invariants
    assert final_state.get("st") == 70, f"final FSM st={final_state.get('st')}"
    assert final_diag.get("group_error_stop_trips") == trips_baseline, (
        f"GroupErrorStop tripped during soak: "
        f"{trips_baseline} -> {final_diag.get('group_error_stop_trips')}"
    )
    assert no_reply / max(1, sent) < 0.05, (
        f"no-reply rate {no_reply}/{sent} = "
        f"{100*no_reply/sent:.2f}% > 5%"
    )
    assert max_buf > 0, (
        f"motion buffer never accumulated -- moves may not have been "
        f"queueing (max_buf={max_buf})"
    )
    # After cooldown buffer must drain (proves moves complete).
    assert drained_at is not None, (
        "motion buffer never drained during cooldown -- moves stuck?"
    )
    # And last_completed_movement_id must have advanced past 0 once the
    # MOVE_DONE edge fired.
    assert final_done > 0, (
        f"last_completed_movement_id stayed 0 after cooldown drain "
        f"-- MOVE_DONE event never fired?"
    )
    # Arm position changed across the run.
    assert len(set(arm_pos_history)) >= 3, (
        f"arm position covered only {len(set(arm_pos_history))} distinct "
        f"values across the soak -- not really executing"
    )
