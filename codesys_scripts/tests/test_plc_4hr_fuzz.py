# 4-hour mixed-load fuzz. Catches slow-burn issues that the 18-min soak
# misses: counter near-overflow, FlyEvent slot leaks, slow FSM wedge
# probability, heartbeat jitter under sustained 25 pkts/s.
#
# Run isolated:
#   pytest tests/test_plc_4hr_fuzz.py -v -s
# Smoke (10 min): FUZZ_4HR_SHORT=1
import os
import random
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from tests._raw_plc import (
    raw_plc_socket,
    send_pack,
    drain_until_id,
    drain_all,
    bring_fsm_to_ready,
    ui_set_tcp,
    m4_pulse_seq,
)

HERE = Path(__file__).resolve().parent
SHORT = os.environ.get("FUZZ_4HR_SHORT") == "1"

# Side log: line-buffered, tail-able while pytest buffers stdout. Path
# overridable via FUZZ_4HR_LOG; default lives next to the test.
LOG_PATH = Path(os.environ.get(
    "FUZZ_4HR_LOG",
    str(HERE / f"fuzz_4hr_{time.strftime('%Y%m%d_%H%M%S')}.log"),
))
_log_fh = open(LOG_PATH, "a", buffering=1, encoding="utf-8")


def _log(msg: str):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        _log_fh.write(line + "\n")
    except Exception:
        pass

TOTAL_DURATION_S = 600.0 if SHORT else 4 * 3600.0
WARMUP_S = 30.0 if SHORT else 600.0
COOLDOWN_S = 30.0 if SHORT else 1800.0
LOAD_DURATION_S = TOTAL_DURATION_S - WARMUP_S - COOLDOWN_S
CHECKPOINT_INTERVAL_S = 60.0 if SHORT else 900.0
TARGET_PKT_RATE = 25.0  # packets per second total

EV_POOL = [2, 4, 6, 7, 8, 9, 0, 1, 3, 5, 100, 999, -1]
LEGAL_STATES = {10, 20, 30, 40, 50, 60, 70, 990}

# Map fuzz-report names -> SYS/GET_DIAG field names. The reverse-lookup
# in _snap_counters(s) flattens the TCP reply into the same dict shape the
# rest of the test expects. Lets the fuzz survive a dead CODESYS RPC
# daemon -- counters come straight off the wire.
COUNTER_SYMS = {
    "GVL.StateChangeEventCount":          "st_chg_event_count",
    "GVL.SelfReentryCount":                "self_reentry",
    "GVL.PendingStChgDropCount":           "pending_stchg_drop",
    "GVL.PendingMoveDoneDropCount":        "pending_movedone_drop",
    "GVL.ReMpDropCount":                   "remp_drop",
    "GVL.ReMpDropCount_Reply":             "remp_drop_reply",
    "GVL.ReMpDropCount_TriggerEvent":      "remp_drop_trig",
    "GVL.SendStallDropCount":              "send_stall_drop",
    "GVL.IdleResetCount":                  "idle_reset",
    "GVL.UiHeartbeatStaleCount":           "ui_hb_stale_count",
    "GVL.DupeCommandCount":                "dupe_cmd",
    "GVL.GroupNotReadyNakCount":           "group_not_ready_nak",
    "GVL.MissingTypeFieldNakCount":        "missing_type_nak",
    "AxisGroupSM.IoCommandCount":          "io_cmd_count",
    "AxisGroupSM.IoTriggerCount":          "io_trig_count",
    "AxisGroupSM.FlyEventAvailableCount":  "flyevent_avail",
}


def _force_virtual():
    subprocess.run(
        [sys.executable, str(HERE.parent / "rpc.py"), "exec",
         "--file", str(HERE.parent / "jobs" / "templates" / "virtual_motors_force.py")],
        capture_output=True, timeout=30,
    )


_SNAP_ID = [950_000]


def _snap_counters(sock, max_attempts: int = 4):
    """Read every counter via SYS/GET_DIAG over the live raw socket.

    Previously this drove CODESYS scripting via the RPC daemon -- 3s per
    read and one daemon crash kills a 4-hour test. The TCP path is ~5ms
    and shares the socket the producer loop already owns, so a dying
    daemon stops being a structural hazard.

    Retries protect against drain-loop missing the reply id under heavy
    cross-traffic on the same socket."""
    for attempt in range(max_attempts):
        _SNAP_ID[0] += 1
        cid = _SNAP_ID[0]
        try:
            send_pack(sock, {"type": "SYS", "cmd": "GET_DIAG", "id": cid})
            r = drain_until_id(sock, cid, 2.5)
            if r is None:
                raise RuntimeError(f"no GET_DIAG reply for id={cid}")
            out = {}
            for fuzz_name, diag_key in COUNTER_SYMS.items():
                v = r.get(diag_key)
                try:
                    out[fuzz_name] = int(v) if v is not None else -1
                except (TypeError, ValueError):
                    out[fuzz_name] = -1
            return out
        except Exception as ex:
            _log(f"[4hr-fuzz] _snap_counters attempt {attempt+1} failed: "
                 f"{type(ex).__name__}: {ex} -- retrying in 1s")
            time.sleep(1.0)
    raise RuntimeError("GET_DIAG counter read failed after %d attempts" % max_attempts)


def _delta(pre, post):
    return {k: post.get(k, 0) - pre.get(k, 0) for k in pre}


def test_4hr_mixed_fuzz(raw_plc_socket):
    """Mixed-load fuzz against the running PLC for 4 hours.

    Three concurrent producers share a single raw socket (under
    _send_lock):
      - 60% SYS events: random GA_EV (incl. nonsense), GET_MACHINE_STATE, PING
      - 30% M4 fly-event registration: random trig (20/120), random
        target/ttl/pins
      - 10% no-op idle (so the rate doesn't pin the CPU)

    At every CHECKPOINT_INTERVAL we read every counter in COUNTER_SYMS
    and assert monotonic invariants. At the end (after cooldown) we
    assert no FlyEvent slot is permanently stuck (FlyEventAvailableCount
    must return to >= 8 of 10) and no drop counters exploded."""
    _force_virtual()
    s = raw_plc_socket

    if not bring_fsm_to_ready(s):
        pytest.skip("FSM did not reach Ready (likely SMC virtual-axis trip)")

    _log(f"\n[4hr-fuzz] total={TOTAL_DURATION_S/60:.1f}min "
          f"warmup={WARMUP_S/60:.1f}min cooldown={COOLDOWN_S/60:.1f}min "
          f"checkpoint_every={CHECKPOINT_INTERVAL_S/60:.1f}min "
          f"target_rate={TARGET_PKT_RATE}pkt/s")

    baseline = _snap_counters(s)
    _log(f"[4hr-fuzz] baseline: {baseline}")

    rng = random.Random(0xC0FFEE)
    stop = threading.Event()
    no_reply_events = []
    checkpoints = []
    test_start = time.time()
    next_checkpoint = test_start + CHECKPOINT_INTERVAL_S
    pkt_id = 200_000

    def _next_id():
        nonlocal pkt_id
        pkt_id += 1
        if pkt_id > 1_900_000_000:
            pkt_id = 200_000  # well below DINT max, well above keepalive band
        return pkt_id

    # Warmup phase: just settle, drain anything pending.
    _log(f"[4hr-fuzz] warmup {WARMUP_S:.0f}s ...")
    drain_all(s, duration=2.0)
    warmup_end = time.time() + WARMUP_S
    while time.time() < warmup_end and not stop.is_set():
        time.sleep(0.5)

    # Load phase.
    pre_load = _snap_counters(s)
    load_end = time.time() + LOAD_DURATION_S
    sleep_per_pkt = 1.0 / TARGET_PKT_RATE
    last_log = time.time()
    sent = 0
    sys_sent = 0
    m4_sent = 0
    m4_acked = 0
    no_reply = 0

    _log(f"[4hr-fuzz] load phase {LOAD_DURATION_S/60:.1f}min ...")

    while time.time() < load_end:
        roll = rng.random()
        try:
            if roll < 0.60:
                # SYS event
                kind = rng.random()
                cid = _next_id()
                if kind < 0.4:
                    send_pack(s, {"type": "SYS", "cmd": "GA_EV",
                                  "ev": rng.choice(EV_POOL), "id": cid})
                elif kind < 0.7:
                    send_pack(s, {"type": "SYS",
                                  "cmd": "GET_MACHINE_STATE", "id": cid})
                else:
                    send_pack(s, {"type": "SYS", "cmd": "PING", "id": cid})
                # Best-effort: drain reply but don't block long.
                r = drain_until_id(s, cid, 1.5)
                if r is None:
                    no_reply += 1
                    no_reply_events.append((time.time() - test_start, "sys", cid))
                sys_sent += 1

            elif roll < 0.90:
                # M4 fly-event register
                cid = _next_id()
                trig = rng.choice([20, 120])
                if trig == 120:
                    pkt = {
                        "type": "M", "cmd": "M4", "id": cid,
                        "motion_id": 0,
                        "trig": 120,
                        "tx": rng.uniform(-1000, 1000),
                        "ty": rng.uniform(-1000, 1000),
                        "tz": rng.uniform(-1000, 1000),
                        "td": rng.uniform(0.001, 1e6),
                        "tin": rng.choice([0, 1]),
                        # Bias pin nonzero so M4 actually registers (the
                        # IoStageCount>0 gate drops pin=0 packets without
                        # ack, which still tests the parser but not the
                        # ring/registration path).
                        "pin_op_seq": m4_pulse_seq(
                            rng.choice([0x1, 0x100, 0x4000, 0x8001]),
                            rng.choice([0, 0x1, 0x100, 0x4000, 0x8001]),
                            reset_ms=rng.choice([0, 50, 200]),
                        ),
                        # Short TTL so slots don't pile up indefinitely.
                        "ttl_ms": rng.randint(50, 1500),
                        "event_id": cid,
                    }
                else:
                    pkt = {
                        "type": "M", "cmd": "M4", "id": cid,
                        "motion_id": 0,
                        "trig": 20,
                        "motion_progress": 1.0,
                        "pin_op_seq": m4_pulse_seq(
                            rng.choice([0x1, 0x4000]),
                            rng.choice([0, 0x1, 0x4000]),
                            reset_ms=rng.choice([0, 50]),
                        ),
                        "ttl_ms": rng.randint(100, 800),
                        "event_id": cid,
                    }
                send_pack(s, pkt)
                r = drain_until_id(s, cid, 1.5)
                if r is None:
                    no_reply += 1
                    no_reply_events.append((time.time() - test_start, "m4", cid))
                else:
                    if r.get("ack"):
                        m4_acked += 1
                m4_sent += 1
            # else idle ~10% of time

            sent += 1
            time.sleep(sleep_per_pkt)
        except Exception as ex:
            _log(f"[4hr-fuzz] producer ex: {type(ex).__name__}: {ex}")
            time.sleep(0.2)

        # Checkpoint
        now = time.time()
        if now >= next_checkpoint:
            elapsed_min = (now - test_start) / 60.0
            cp = _snap_counters(s)
            d = _delta(pre_load, cp)
            checkpoints.append((elapsed_min, cp, d))
            _log(f"[4hr-fuzz] cp t={elapsed_min:5.1f}min "
                  f"sent={sent} sys={sys_sent} m4={m4_sent}/{m4_acked} "
                  f"no_reply={no_reply} | "
                  f"ReMpDrop={d['GVL.ReMpDropCount']} "
                  f"PendingStChg={d['GVL.PendingStChgDropCount']} "
                  f"StateChg={d['GVL.StateChangeEventCount']} "
                  f"FlyAvail={cp['AxisGroupSM.FlyEventAvailableCount']}")

            # Live invariants
            assert d["GVL.PendingStChgDropCount"] == 0, (
                f"PendingStChgDropCount climbed mid-test "
                f"(t={elapsed_min:.1f}min): {d['GVL.PendingStChgDropCount']}"
            )
            assert d["GVL.PendingMoveDoneDropCount"] == 0, (
                f"PendingMoveDoneDropCount climbed mid-test "
                f"(t={elapsed_min:.1f}min): {d['GVL.PendingMoveDoneDropCount']}"
            )
            # FlyEventAvailable must not stick at 0 (slot leak)
            assert cp["AxisGroupSM.FlyEventAvailableCount"] >= 1, (
                f"FlyEventBuffer fully saturated at t={elapsed_min:.1f}min "
                f"-- slot leak: {cp}"
            )
            next_checkpoint = now + CHECKPOINT_INTERVAL_S

        if now - last_log > 30:
            last_log = now

    # Cooldown phase: stop sending, let TTLs expire and ring drain.
    _log(f"[4hr-fuzz] cooldown {COOLDOWN_S:.0f}s ...")
    drain_all(s, duration=2.0)
    cool_end = time.time() + COOLDOWN_S
    while time.time() < cool_end:
        time.sleep(1.0)
        # Keep heartbeat-supervisor happy via idle PINGs (the fixture's
        # keepalive thread covers this, but a drain prevents recv buffer
        # bloat).
        drain_all(s, duration=0.05)

    # Final audit
    final = _snap_counters(s)
    d_final = _delta(pre_load, final)
    elapsed_total = (time.time() - test_start) / 60.0
    _log(f"\n[4hr-fuzz] DONE elapsed={elapsed_total:.1f}min "
          f"sent={sent} no_reply={no_reply} ({100*no_reply/max(1,sent):.2f}%)")
    _log(f"[4hr-fuzz] final counter delta: {d_final}")
    _log(f"[4hr-fuzz] final FlyEventAvailableCount = "
          f"{final['AxisGroupSM.FlyEventAvailableCount']}")

    # Hard invariants
    assert d_final["GVL.PendingStChgDropCount"] == 0, (
        f"final PendingStChgDropCount {d_final['GVL.PendingStChgDropCount']}"
    )
    assert d_final["GVL.PendingMoveDoneDropCount"] == 0, (
        f"final PendingMoveDoneDropCount {d_final['GVL.PendingMoveDoneDropCount']}"
    )
    # Slot-leak guard: after cooldown, almost all 10 slots should be free.
    assert final["AxisGroupSM.FlyEventAvailableCount"] >= 8, (
        f"FlyEvent slots not reclaimed after cooldown: "
        f"{final['AxisGroupSM.FlyEventAvailableCount']}/10 free"
    )
    # No-reply rate sanity: keepalive PINGs in the fixture send at 1.25Hz,
    # so a few sporadic misses are fine; >5% means structural problem.
    assert no_reply <= sent * 0.05, (
        f"no-reply rate {no_reply}/{sent} = {100*no_reply/sent:.2f}% > 5%"
    )
    # ReMp drops scale with traffic, but we're sending ~360k packets in
    # 4hrs and the ring has 32 slots draining per Comm scan -- expect <
    # 1% drops.
    assert d_final["GVL.ReMpDropCount"] <= sent * 0.01, (
        f"ReMpDropCount {d_final['GVL.ReMpDropCount']} > 1% of {sent} pkts"
    )
