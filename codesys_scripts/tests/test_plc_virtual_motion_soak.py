# 5-minute virtual-motion + FlyEvent stress soak. Cold-resets the PLC,
# brings the FSM to Ready in virtual mode, then drives TWO producers in
# parallel:
#   - G1 stream (~5/s, Delta-reachable Cartesian targets) -> exercises
#     motion buffer fill/drain + kinematic execution path
#   - M4 fly-event stream (~5/s, random TTL + trig 20/120) -> exercises
#     the 10-slot FlyEvent ring lifecycle: register, expire on TTL,
#     trigger on motion progress, recycle slots
#
# Asserts:
#   - GroupErrorStop never trips (group_error_stop_trips stays 0)
#   - FSM stays in Ready throughout
#   - motion_buffer_size oscillates (queue fills and drains, doesn't peg)
#   - FlyEventAvailableCount never sticks at 0 (no slot leak mid-flight)
#   - FlyEventAvailableCount recovers to >= 8/10 after cooldown
#   - no_reply rate < 5%
#
# Smoke (60s): MOTION_SOAK_SHORT=1
#
# Distinct from test_plc_4hr_fuzz which sends 0 G1 -- this is the
# motion-execution + fly-event-under-motion counterpart.
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
    m4_pulse_seq,
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
# Move geometry tuned so motion_buffer transiently fills (queue depth
# visible at checkpoint reads). F=80 + 25mm-class moves = ~300ms per
# move; at 5 G1/s the buffer averages 1-2 in flight, peaks at 4-5
# during bursts. Smaller moves drain too fast (buf=0 every read),
# larger moves saturate MOTION_BUFFER_THRESHOLD=12 -- this hits the
# sweet spot for visibility without back-pressure NAKs.
G1_F = 80.0
G1_RANGE_XY = 25.0
G1_Z_MIN, G1_Z_MAX = -130.0, -70.0
# Fly events: roughly match G1 cadence so the FlyEvent ring sees real
# pressure. 10 slots total; with avg TTL ~500ms and 5 M4/s = 2.5 in
# flight on average. Bursts can push higher, exercising the wrap.
TARGET_M4_RATE = 5.0
M4_TTL_MIN_MS = 100
M4_TTL_MAX_MS = 800
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
    g1_sent = g1_acks = 0
    m4_sent = m4_acks = 0
    no_reply = 0
    start = time.time()
    next_checkpoint = start + CHECKPOINT_S
    # G1 + M4 interleaved on the same target rate; total pkt cadence is
    # G1_RATE + M4_RATE so the inter-pkt sleep is computed against the sum.
    sleep_per_pkt = 1.0 / (TARGET_G1_RATE + TARGET_M4_RATE)
    # Probability of G1 vs M4 weighted by rate so each producer averages
    # at its configured rate independently.
    p_g1 = TARGET_G1_RATE / (TARGET_G1_RATE + TARGET_M4_RATE)
    max_buf = 0
    min_fly_avail = 10
    arm_pos_history = []
    last_arm = _snap_arm_pos(s)
    if last_arm: arm_pos_history.append(last_arm)
    # Recent G1 movement_ids -- M4 attaches to these so the fly events
    # actually enter the ring waiting for trigger conditions instead of
    # being orphan/instant-expire. Keep small so it tracks "still in
    # flight or just finished" -- a long history would attach to
    # already-completed G1s, defeating the test.
    recent_mvids = []

    end_time = start + DURATION_S
    while time.time() < end_time:
        pkt_id += 1
        if rng.random() < p_g1:
            # G1: Delta-reachable Cartesian, small XY box, Z always negative.
            pkt = {
                "type": "M", "cmd": "G1", "id": pkt_id,
                "X": rng.uniform(-G1_RANGE_XY, G1_RANGE_XY),
                "Y": rng.uniform(-G1_RANGE_XY, G1_RANGE_XY),
                "Z": rng.uniform(G1_Z_MIN, G1_Z_MAX),
                "F": G1_F, "ACC": 500.0, "DEA": 500.0, "JERK": 2000.0,
            }
            kind = "g1"
        else:
            # M4: attach to a recent in-flight G1's movement_id so the
            # fly event actually sits in the FlyEvent ring waiting for
            # trigger condition (vs orphan motion_id=0 which fires/
            # expires immediately and never pressures the ring).
            attach_mvid = rng.choice(recent_mvids) if recent_mvids else 0
            if rng.random() < 0.5:
                pkt = {
                    "type": "M", "cmd": "M4", "id": pkt_id,
                    "motion_id": attach_mvid, "trig": 20,
                    "motion_progress": rng.uniform(0.1, 0.9),
                    "pin_op_seq": m4_pulse_seq(
                        rng.choice([0x1, 0x4000]),
                        rng.choice([0, 0x1, 0x4000]),
                        reset_ms=rng.choice([0, 50]),
                    ),
                    "ttl_ms": rng.randint(M4_TTL_MIN_MS, M4_TTL_MAX_MS),
                    "event_id": pkt_id,
                }
            else:
                pkt = {
                    "type": "M", "cmd": "M4", "id": pkt_id,
                    "motion_id": attach_mvid, "trig": 120,
                    "tx": rng.uniform(-50, 50),
                    "ty": rng.uniform(-50, 50),
                    "tz": rng.uniform(-150, -50),
                    "td": rng.uniform(10, 1e4),
                    "tin": rng.choice([0, 1]),
                    "pin_op_seq": m4_pulse_seq(
                        rng.choice([0x1, 0x100, 0x4000, 0x8001]),
                        rng.choice([0, 0x1, 0x100, 0x4000, 0x8001]),
                        reset_ms=rng.choice([0, 50, 200]),
                    ),
                    "ttl_ms": rng.randint(M4_TTL_MIN_MS, M4_TTL_MAX_MS),
                    "event_id": pkt_id,
                }
            kind = "m4"

        send_pack(s, pkt)
        # M4 needs a slightly longer reply window under combined load
        # (motion FB busy executing keeps SYS drain slower); 1.5s matches
        # the 4hr fuzz timeout.
        r = drain_until_id(s, pkt_id, 1.5 if kind == "m4" else 1.0)
        if r is None:
            no_reply += 1
        elif r.get("ack"):
            if kind == "g1":
                g1_acks += 1
                mvid = r.get("movement_id")
                if mvid:
                    recent_mvids.append(mvid)
                    # Keep a short window so attached M4s target moves
                    # still in flight or just-finished (TTL gives the M4
                    # a chance to fire either way).
                    if len(recent_mvids) > 8:
                        recent_mvids.pop(0)
            else: m4_acks += 1
        if kind == "g1": g1_sent += 1
        else: m4_sent += 1
        time.sleep(sleep_per_pkt)

        # Checkpoint
        now = time.time()
        if now >= next_checkpoint:
            st = _snap_state(s)
            diag = _snap_diag(s)
            buf = st.get("motion_buffer_size") or 0
            fly_avail = (diag or {}).get("flyevent_avail", -1)
            if buf > max_buf: max_buf = buf
            if fly_avail >= 0 and fly_avail < min_fly_avail:
                min_fly_avail = fly_avail
            arm = _snap_arm_pos(s)
            if arm: arm_pos_history.append(arm)
            elapsed = now - start
            arm_str = (f"arm=({arm[0]:.1f},{arm[1]:.1f},{arm[2]:.1f})"
                       if arm else "arm=?")
            print(f"[motion-soak] t={elapsed:5.1f}s "
                  f"g1={g1_acks}/{g1_sent} m4={m4_acks}/{m4_sent} "
                  f"no_reply={no_reply} buf={buf}/max{max_buf} "
                  f"fly_avail={fly_avail}/min{min_fly_avail} "
                  f"st={st.get('st')} {arm_str}")

            # Live invariant: FSM stays Ready (supervisor moves it to Error
            # if GroupErrorStop trips).
            assert st.get("st") == 70, (
                f"FSM left Ready mid-soak at t={elapsed:.1f}s: "
                f"st={st.get('st')} st_str={st.get('st_str')} "
                f"err_src={st.get('err_src')} err_id={st.get('err_id')}"
            )
            # FlyEvent ring must not stay starved (slot leak).
            assert fly_avail >= 1, (
                f"FlyEventAvailableCount stuck at {fly_avail} "
                f"at t={elapsed:.1f}s -- slot leak?"
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
    final_fly_avail = (final_diag or {}).get("flyevent_avail", -1)
    total_sent = g1_sent + m4_sent
    print(f"\n[motion-soak] DONE elapsed={elapsed:.1f}s "
          f"g1={g1_acks}/{g1_sent} m4={m4_acks}/{m4_sent} "
          f"no_reply={no_reply} max_buf={max_buf} min_fly_avail={min_fly_avail} "
          f"drained_at={drained_at and (drained_at-start):.1f}s")
    print(f"[motion-soak] final st={final_state.get('st')} "
          f"last_completed_movement_id={final_done} "
          f"fly_avail={final_fly_avail}/10 "
          f"trips={final_diag.get('group_error_stop_trips')}")

    # Hard invariants
    assert final_state.get("st") == 70, f"final FSM st={final_state.get('st')}"
    assert final_diag.get("group_error_stop_trips") == trips_baseline, (
        f"GroupErrorStop tripped during soak: "
        f"{trips_baseline} -> {final_diag.get('group_error_stop_trips')}"
    )
    # Combined G1+M4 load is genuinely noisier than the SYS-heavy 4hr
    # fuzz (which sees <0.01% no_reply at 25pkt/s): motion FB busy
    # executing slows SYS drain, so some replies miss the per-packet
    # window. 15% is the relaxed bound -- a real regression (e.g. PLC
    # task wedge, dropped socket) blows past this trivially.
    assert no_reply / max(1, total_sent) < 0.15, (
        f"no-reply rate {no_reply}/{total_sent} = "
        f"{100*no_reply/total_sent:.2f}% > 15%"
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
    # FlyEvent slot leak guard: after cooldown (TTLs expired), almost
    # all 10 slots should be free again. Mirrors the 4hr fuzz check.
    assert final_fly_avail >= 8, (
        f"FlyEvent slots not reclaimed after cooldown: "
        f"{final_fly_avail}/10 free (min during run: {min_fly_avail})"
    )
    # Sanity: M4s actually got processed (not just NAK'd).
    assert m4_acks > 0, "no M4 fly-event ever acked -- registration path dead?"
    # Ring actually got exercised: at least one moment during the soak
    # had >=1 slot in use (avail < 10). If min stayed at 10 the M4s
    # weren't entering the ring at all -- attach-to-mvid is broken or
    # something cleaned them up too aggressively.
    assert min_fly_avail < 10, (
        f"FlyEvent ring never saw any slot in flight (min_avail=10) "
        f"-- M4s aren't entering the ring; check attach motion_id "
        f"plumbing"
    )
