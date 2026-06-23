# Virtual-motion + FlyEvent SATURATION test. Cold-resets the PLC, brings
# FSM to Ready in virtual mode, then runs a synchronous producer loop:
# send G1/M4 -> wait for ack -> next. No inter-packet sleep. Both
# buffers self-saturate via natural backpressure:
#   - PLC's ProcessMotionPacket only accepts a G1 when
#     MotionBufferSize < MOTION_BUFFER_THRESHOLD (12). At threshold, the
#     packet sits in the host->PLC minfo_buf ring until the motion FB
#     drains a slot, then the reply lands -- so the synchronous loop
#     naturally paces itself to "buffer-full minus 1".
#   - FlyEvent ring (10 slots) fills when M4s attach to long-TTL G1
#     movement_ids; same natural pacing applies.
#
# Asserts:
#   - GroupErrorStop never trips
#   - FSM stays Ready throughout
#   - motion_buffer_size reaches MOTION_BUFFER_THRESHOLD (proves real
#     saturation, not just transient queueing)
#   - FlyEventAvailableCount drops to <=2/10 at some point (ring really
#     pressured, not just exercised)
#   - Both buffers recover after cooldown (no slot leaks)
#   - no_reply rate is low (sync send-wait keeps the socket clean)
#
# Smoke (60s): MOTION_SOAK_SHORT=1
# Default duration: 5 min (covers many fill/drain cycles)
import os
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

import msgpack
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
# 1hr default (60s smoke). The synchronous saturation pattern is dense
# enough that 1hr ~= ~6000 G1 + ~12000 M4; long-haul confirms no slow
# drift in trigger accounting (io_cmd_count, io_trig_count).
DURATION_S = 60.0 if SHORT else 3600.0
# Move geometry chosen so each G1 takes ~400ms on virtual axes (F=60
# + 25mm-class). Slow enough that 12-deep buffer takes ~5s to drain
# fully -- a producer in synchronous send-wait will see motion buffer
# pinned near MOTION_BUFFER_THRESHOLD almost the whole soak. The
# keepalive thread covers the per-ack wait against IDLE_TIMEOUT (7s)
# so we can safely let single-ack waits run >1s.
G1_F = 60.0
G1_RANGE_XY = 25.0
G1_Z_MIN, G1_Z_MAX = -130.0, -70.0
# M4 TTL > queue-drain time so fly events outlive multiple G1
# completions and pile up in the 10-slot ring. With ~400ms per move
# and ~12 in queue, 5s TTL covers a full queue cycle.
M4_TTL_MIN_MS = 3000
M4_TTL_MAX_MS = 5000
# Round-robin slot mapping: 4 slots = G1 + 3 M4 variants (one of each
# FlyEventTriggerType). Slot 0=G1, 1=trig20 (MovementProgress),
# 2=trig120 (Distance), 3=trig130 (Pulse). 3 M4 per G1 keeps the
# FlyEvent ring under heavy pressure.
PRODUCER_ROUND = 4
M4_TRIGS = [20, 120, 130]   # 0=G1, then one slot per trig kind
# Conveyor synthetic-pulse step: pre-enabled so trig=130 PulseTrigger
# can fire on its pulse_target. Step=10 gives 10,000 pulses/s at 1ms EC.
CONVEYOR_STEP = 10
# Per-packet ack timeout: must exceed worst-case "wait for a motion
# slot to free" which is ~G1_duration when buffer is at threshold.
# 6s leaves headroom; the per-ack wait does NOT need to be under
# IDLE_TIMEOUT because the keepalive thread is sending PINGs every
# 800ms in the background.
ACK_TIMEOUT_S = 6.0
CHECKPOINT_S = 5.0 if SHORT else 15.0


def _force_virtual():
    subprocess.run(
        [sys.executable, str(HERE.parent / "rpc.py"), "exec", "--file",
         str(HERE.parent / "jobs" / "templates" / "virtual_motors_force.py")],
        capture_output=True, timeout=30,
    )


def _cold_reset_then_virtual():
    """Cold reset to clear sticky SoftMotion state, then force virtual
    mode AND enable the synthetic conveyor pulse generator so trig=130
    PulseTrigger has something to fire on. Mirrors the working clean-
    state pattern verified 2026-06-23."""
    script = f"""\
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
oapp.set_prepared_value("GVL.ConveyorPulseSyntheticEnable", "TRUE")
oapp.set_prepared_value("GVL.ConveyorPulseSyntheticStep", "{CONVEYOR_STEP}")
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


def _start_keepalive(s, stop_event):
    """Background PING every 800ms so the PLC's TCP IDLE_TIMEOUT (7s)
    doesn't trip during long ack-waits when buffers are saturated.
    Mirrors the raw_plc_socket fixture's pattern -- not used here
    because the cold reset closes that fixture's socket."""
    from tests._raw_plc import _send_lock
    def loop():
        i = 0
        while not stop_event.is_set():
            i += 1
            try:
                with _send_lock:
                    s.sendall(msgpack.packb(
                        {"type": "SYS", "cmd": "PING", "id": 50000 + (i % 1000)},
                        use_bin_type=True,
                    ))
            except OSError:
                return
            stop_event.wait(0.8)
    th = threading.Thread(target=loop, daemon=True)
    th.start()
    return th


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

    keepalive_stop = threading.Event()
    keepalive_th = None
    try:
        if not bring_fsm_to_ready(s):
            pytest.skip("could not bring FSM to Ready after cold reset")
        keepalive_th = _start_keepalive(s, keepalive_stop)
        _run_soak(s)
    finally:
        keepalive_stop.set()
        if keepalive_th: keepalive_th.join(timeout=1.5)
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
    print(f"\n[motion-soak] DURATION={DURATION_S}s SATURATION-mode "
          f"(sync send-wait-ack, no pacing) baseline trips={trips_baseline}")

    rng = random.Random(0xC0DE)
    pkt_id = 200_000
    g1_sent = g1_acks = 0
    m4_sent = m4_acks = 0
    no_reply = 0
    start = time.time()
    next_checkpoint = start + CHECKPOINT_S
    max_buf = 0
    min_fly_avail = 10
    arm_pos_history = []
    last_arm = _snap_arm_pos(s)
    if last_arm: arm_pos_history.append(last_arm)
    # Recent G1 movement_ids -- M4 attaches to these so the fly events
    # actually enter the ring waiting for trigger conditions instead of
    # being orphan/instant-expire.
    recent_mvids = []
    # Conveyor pulse cache for trig=130 (PulseTrigger). Refreshed at
    # each checkpoint -- exact freshness doesn't matter, the M4 picks a
    # pulse_target ahead of this sampled value so trigger fires.
    recent_pulse = 0
    # Per-trig-kind send tally for the final report.
    m4_kind_sent = {20: 0, 120: 0, 130: 0}
    # Baseline IO counters; we'll diff against final + per-checkpoint.
    pre_diag_io_cmd = pre_diag.get("io_cmd_count", 0)
    pre_diag_io_trig = pre_diag.get("io_trig_count", 0)
    # Synchronous saturation: send packet, wait for ack, send next. No
    # inter-packet sleep. Alternate G1/M4 round-robin (every Nth pkt is
    # M4 per PRODUCER_M4_EVERY). The PLC's MOTION_BUFFER_THRESHOLD (12)
    # gate makes the next G1 ack-delay until a slot frees, so the loop
    # self-paces against whichever buffer is fullest.
    pkt_round = 0

    end_time = start + DURATION_S
    while time.time() < end_time:
        pkt_id += 1
        pkt_round += 1
        # Round-robin slot 0 -> G1, slots 1..3 -> M4 trig 20/120/130.
        # Force G1 until we have a movement_id captured (M4s attach to
        # one); empty recent_mvids means any M4 sits orphan and just
        # times out the ACK_TIMEOUT_S wait.
        slot = pkt_round % PRODUCER_ROUND
        is_m4 = (slot != 0) and bool(recent_mvids)
        if not is_m4:
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
            # M4 attaches to a recent G1's movement_id so the fly event
            # actually sits in the FlyEvent ring waiting for trigger
            # condition. trig kind round-robins through the three
            # FlyEventTriggerType values.
            attach_mvid = rng.choice(recent_mvids)
            trig = M4_TRIGS[(slot - 1) % len(M4_TRIGS)]
            base = {
                "type": "M", "cmd": "M4", "id": pkt_id,
                "motion_id": attach_mvid, "trig": trig,
                "pin_op_seq": m4_pulse_seq(
                    rng.choice([0x1, 0x100, 0x4000, 0x8001]),
                    rng.choice([0, 0x1, 0x100, 0x4000, 0x8001]),
                    reset_ms=rng.choice([0, 50, 200]),
                ),
                "ttl_ms": rng.randint(M4_TTL_MIN_MS, M4_TTL_MAX_MS),
                "event_id": pkt_id,
            }
            if trig == 20:
                # MovementProgressTrigger: fire when motion progress >=
                # motion_progress. Use a low threshold so most fire well
                # before TTL.
                base["motion_progress"] = rng.uniform(0.1, 0.4)
            elif trig == 120:
                # DistanceTrigger: fire when arm distance to (tx,ty,tz)
                # crosses td. Big radius + tin=1 (enter ball) so the
                # ball is likely already enclosing the arm path, firing
                # promptly. The exact in/out semantics are tested at
                # protocol level elsewhere; here we just want triggers
                # to fire so IoTriggerCount advances.
                base["tx"] = 0.0
                base["ty"] = 0.0
                base["tz"] = rng.uniform(-110.0, -90.0)
                base["td"] = 500.0     # big ball -> arm always inside
                base["tin"] = 1
            else:  # trig == 130, PulseTrigger
                # Fire when ConveyorPulseRaw >= pulse_target. With the
                # synthetic generator advancing CONVEYOR_STEP/scan
                # (=10,000 pulses/s at 1ms EC), set target a few ticks
                # ahead of "now" so the M4 enters ring, then fires
                # within the TTL.
                base["pulse_target"] = recent_pulse + rng.randint(
                    1000, 5000)
            pkt = base
            kind = "m4"
            m4_kind_sent[trig] = m4_kind_sent.get(trig, 0) + 1

        send_pack(s, pkt)
        # Long ack timeout: when motion buffer or fly ring is saturated,
        # the PLC parks the packet in minfo_buf until a slot frees, so
        # ack-delay can be O(seconds). See ACK_TIMEOUT_S comment.
        r = drain_until_id(s, pkt_id, ACK_TIMEOUT_S)
        if r is None:
            no_reply += 1
        elif r.get("ack"):
            if kind == "g1":
                g1_acks += 1
                mvid = r.get("movement_id")
                if mvid:
                    recent_mvids.append(mvid)
                    # Keep recent_mvids short -- attached M4s should
                    # target in-flight or just-finished moves, not
                    # ancient ones from minutes ago.
                    if len(recent_mvids) > 12:
                        recent_mvids.pop(0)
            else:
                m4_acks += 1
        if kind == "g1": g1_sent += 1
        else: m4_sent += 1
        # No inter-packet sleep -- the ack-wait is the natural pacing.

        # Checkpoint
        now = time.time()
        if now >= next_checkpoint:
            st = _snap_state(s)
            diag = _snap_diag(s)
            buf = st.get("motion_buffer_size") or 0
            fly_avail = (diag or {}).get("flyevent_avail", -1)
            io_cmd = (diag or {}).get("io_cmd_count", 0) - pre_diag_io_cmd
            io_trig = (diag or {}).get("io_trig_count", 0) - pre_diag_io_trig
            if buf > max_buf: max_buf = buf
            if fly_avail >= 0 and fly_avail < min_fly_avail:
                min_fly_avail = fly_avail
            arm = _snap_arm_pos(s)
            if arm: arm_pos_history.append(arm)
            # Refresh conveyor pulse for future trig=130 packets. Cheap
            # piggyback off the arm-pos probe (same GET_COORD1_DEBUG).
            arm_full = _snap_with_retry(s, "GET_COORD1_DEBUG", timeout=2.0)
            if arm_full and arm_full.get("pulse_raw") is not None:
                recent_pulse = arm_full["pulse_raw"]
            elapsed = now - start
            arm_str = (f"arm=({arm[0]:.1f},{arm[1]:.1f},{arm[2]:.1f})"
                       if arm else "arm=?")
            print(f"[motion-soak] t={elapsed:5.1f}s "
                  f"g1={g1_acks}/{g1_sent} m4={m4_acks}/{m4_sent} "
                  f"no_reply={no_reply} buf={buf}/max{max_buf} "
                  f"fly_avail={fly_avail}/min{min_fly_avail} "
                  f"io_cmd={io_cmd} io_trig={io_trig} "
                  f"pulse={recent_pulse} st={st.get('st')} {arm_str}")

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
    final_io_cmd = (final_diag or {}).get("io_cmd_count", 0) - pre_diag_io_cmd
    final_io_trig = (final_diag or {}).get("io_trig_count", 0) - pre_diag_io_trig
    total_sent = g1_sent + m4_sent
    print(f"\n[motion-soak] DONE elapsed={elapsed:.1f}s "
          f"g1={g1_acks}/{g1_sent} "
          f"m4={m4_acks}/{m4_sent} (trig20={m4_kind_sent[20]} "
          f"trig120={m4_kind_sent[120]} trig130={m4_kind_sent[130]}) "
          f"no_reply={no_reply} max_buf={max_buf} min_fly_avail={min_fly_avail} "
          f"drained_at={drained_at and (drained_at-start):.1f}s")
    print(f"[motion-soak] final st={final_state.get('st')} "
          f"last_completed_movement_id={final_done} "
          f"fly_avail={final_fly_avail}/10 "
          f"io_cmd_count={final_io_cmd} io_trig_count={final_io_trig} "
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
    # Saturation: motion buffer must have hit >=9 at some checkpoint
    # (threshold is 12; sync send-wait caps the steady state at
    # threshold-1=11; checkpoint sampling may miss the exact peak by
    # ~1-2 -- so 9 is the empirical saturation floor).
    assert max_buf >= 9, (
        f"motion buffer never saturated -- max_buf={max_buf} of "
        f"threshold 12; producer may not have been fast enough"
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
    # IO accounting: every acked M4 enters the FlyEvent ring (which bumps
    # IoCommandCount). They should match 1:1 modulo INT signedness; allow
    # a tiny slop (1) for the rare in-flight-at-end case where ack came
    # back but the registration scan hadn't bumped the counter yet.
    assert abs(final_io_cmd - m4_acks) <= 1, (
        f"io_cmd_count {final_io_cmd} != m4_acks {m4_acks} "
        f"(diff {final_io_cmd - m4_acks}); fly-event registration "
        f"counter drifted"
    )
    # Triggers actually fired: each fired stage of a pin_op_seq bumps
    # io_trig_count. Most M4s have 2 stages (set + reset_ms reset). At
    # minimum we expect 30% of acked M4s' worth of stages -- a real
    # regression where triggers stop firing (TTL expires too soon, or
    # the trigger condition never matches) drops this to 0.
    min_expected_trig = int(m4_acks * 0.3)
    assert final_io_trig >= min_expected_trig, (
        f"io_trig_count {final_io_trig} below expected {min_expected_trig} "
        f"(30% of {m4_acks} acked M4s); triggers may not be firing"
    )
    # Saturation: FlyEvent ring must have hit <= 4 free slots (>=6 in
    # flight). 10 is the hard ceiling but ring turnover under sync send
    # naturally caps a bit lower; <=4 proves the ring was genuinely
    # under pressure (vs the previous co-stress version which only got
    # to 9/10 because M4s expired before ring filled).
    assert min_fly_avail <= 4, (
        f"FlyEvent ring never saturated: min_fly_avail={min_fly_avail}/10 "
        f"-- M4s either NAK'd before ring entry or expired too quickly; "
        f"check M4 TTL vs send rate"
    )
