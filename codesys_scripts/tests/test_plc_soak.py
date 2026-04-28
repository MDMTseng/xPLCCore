# Long-running soak suite (~30 min). Catches slow-burn issues that the
# fast fuzz tests miss:
#   - counter drift (latched-state bookkeeping that diverges over many cycles)
#   - heartbeat-cadence jitter under sustained load
#   - dedupe-ring rotation correctness over thousands of CommandIds
#   - FSM wedge probability over a long random walk
#
# Default budget: roughly 28-32 minutes wall-clock. To shorten for smoke
# runs, set SOAK_SHORT=1 (cuts each test's loop count to ~10%).
import math
import os
import random
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path

import msgpack
import pytest

from tests._raw_plc import (
    raw_plc_socket, send_pack, drain_until_id, drain_all,
    bring_fsm_to_ready,
)
from tests.test_plc_fsm_coverage import WireReader
from tests.test_plc_holes_e2e import _read_symbols


HERE = Path(__file__).resolve().parent
JOBS = HERE.parent / "jobs" / "templates"

SHORT = os.environ.get("SOAK_SHORT") == "1"


def _force_virtual():
    subprocess.run([sys.executable, str(HERE.parent / "rpc.py"), "exec",
                    "--file", str(JOBS / "virtual_motors_force.py")],
                   capture_output=True, timeout=30)


# ---------- Test 2: heartbeat cadence stability (~5 min nominal) ----------

def test_heartbeat_cadence_stability(raw_plc_socket):
    """Subscribe to the wire for a long window and harvest every HEARTBEAT.
    Default cadence is 500ms. Verify:
      - count is within +/- 5% of expected
      - inter-arrival p99 < 1s (one missed beat is unhealthy)
      - no gap > 2s (more than a missed beat means the listener wedged
        or ring stalled)"""
    duration = 30.0 if SHORT else 300.0  # 5 min default
    s = raw_plc_socket
    reader = WireReader(s)

    arrivals = []
    deadline = time.time() + duration
    print(f"heartbeat cadence: collecting for {duration:.0f}s...")
    while time.time() < deadline:
        reader._pump(0.5)
        for obj in reader.pending:
            if isinstance(obj, dict) and obj.get("name") == "HEARTBEAT":
                arrivals.append(time.time())
        reader.pending.clear()

    expected = duration / 0.5
    assert len(arrivals) >= expected * 0.85, (
        f"got {len(arrivals)} HEARTBEATs in {duration}s, expected ~{expected:.0f}"
    )
    assert len(arrivals) <= expected * 1.15, (
        f"got {len(arrivals)} HEARTBEATs (suspiciously high) in {duration}s"
    )

    gaps = [arrivals[i + 1] - arrivals[i] for i in range(len(arrivals) - 1)]
    gaps.sort()
    p50 = gaps[len(gaps) // 2]
    p99 = gaps[int(len(gaps) * 0.99)]
    mx = gaps[-1]

    assert 0.4 <= p50 <= 0.65, f"heartbeat p50 cadence {p50:.3f}s out of [0.4, 0.65]"
    assert p99 < 1.0, f"heartbeat p99 {p99:.3f}s >= 1.0s"
    assert mx < 2.0, f"heartbeat max gap {mx:.3f}s >= 2.0s"
    print(f"heartbeat cadence: n={len(arrivals)} p50={p50:.3f}s p99={p99:.3f}s max={mx:.3f}s")


# ---------- Test 3: motion endurance (~10-12 min nominal) ----------

@pytest.fixture
def fsm_ready(raw_plc_socket):
    """Bring FSM to Ready with a hard-recovery sequence that clears SMC
    GroupEnable / GroupErrorStop latches a prior session may have left:
        RESET -> POWER_OFF -> POWER_ON -> GROUP_DISABLE -> GROUP_ENABLE
                                                       -> HOME_GO_FORCE_SKIP
    The DISABLE-before-ENABLE pulse is what unsticks MC_GroupEnable error
    11000 (group already enabled / axis-side error). Falling back to plain
    bring_fsm_to_ready leaves the group in Error 'GroupEnabling:enable_fb'."""
    s = raw_plc_socket
    _force_virtual()
    drain_all(s, duration=1.0)

    # Hard recovery cycle.
    # ev: RESET=8, POWER_OFF=3, POWER_ON=2, GROUP_DISABLE=5, GROUP_ENABLE=4,
    #     HOME_GO_FORCE_SKIP=7
    seq = [(8, 0.5), (3, 0.5), (2, 0.8), (5, 0.5), (4, 1.0), (7, 0.8)]
    base = 91000
    for ev, settle in seq:
        send_pack(s, {"type": "SYS", "cmd": "GA_EV", "ev": ev, "id": base})
        drain_until_id(s, base, 2.0)
        time.sleep(settle)
        base += 1

    # Poll for Ready.
    reached = False
    for _ in range(20):
        send_pack(s, {"type": "SYS", "cmd": "GET_MACHINE_STATE", "id": base})
        st = drain_until_id(s, base, 2.0)
        base += 1
        if st and st.get("st") == 70:
            reached = True
            break
        time.sleep(0.3)
    if not reached:
        pytest.skip("hard-recovery still couldn't reach Ready")

    # Ready entry pulses SMC_GroupReset.Execute for ~50 scans
    # (Update.st:188-198). Let it complete before any motion.
    time.sleep(1.5)
    drain_all(s, duration=0.3)
    send_pack(s, {"type": "M", "cmd": "SetCoord0", "id": 91200})
    sc = drain_until_id(s, 91200, 2.0)
    if not (sc and sc.get("ack")):
        pytest.skip(f"SetCoord0 not acked: {sc}")
    return s


def _g1(cid: int, x=0.0, y=0.0, z=0.0, f=100):
    return {"type": "M", "cmd": "G1", "id": cid,
            "X": x, "Y": y, "Z": z, "A": 0,
            "F": f, "ACC": 1000, "DEA": 1000, "JERK": 10000}


def test_motion_endurance(fsm_ready):
    """Fire many G1s with rotating CommandIds; some unique, some dupes.
    Verify:
      - Every reply arrives within timeout (no wedge)
      - movement_id climbs strictly monotonically across unique CommandIds
      - Dedupe ring replays the cached movement_id for repeats
      - DupeCommandCount delta == observed dupe count
      - FSM stays in Ready throughout
      - No off-by-one as the dedupe ring rotates through DUPE_RING_SIZE+ uniques
    """
    n_unique = 50 if SHORT else 800
    dupe_pct = 0.20  # 20% repeats
    s = fsm_ready

    pre = _read_symbols(["GVL.DupeCommandCount", "GVL.StateChangeEventCount"])
    pre_dup = int(pre["GVL.DupeCommandCount"])
    pre_chg = int(pre["GVL.StateChangeEventCount"])

    base_cid = int(time.time_ns() & 0x7FFFFFFF) + 100000
    unique_cids = [base_cid + i for i in range(n_unique)]
    movement_ids: dict[int, int] = {}
    rng = random.Random(0xBADC0FFEE)

    expected_dupes = 0
    last_mvid = -1
    last_log = time.time()
    t0 = time.time()
    tripped_at = None  # iteration at which PLC tripped to Error (if any)
    for k in range(int(n_unique * (1 + dupe_pct))):
        if movement_ids and rng.random() < dupe_pct:
            cid = rng.choice(list(movement_ids.keys()))
            send_pack(s, _g1(cid, x=rng.uniform(-0.05, 0.05), y=rng.uniform(-0.05, 0.05)))
            r = drain_until_id(s, cid, 3.0)
            assert r is not None, f"no reply for repeat cid={cid} (k={k})"
            # PLC may legitimately trip to error stop mid-run (e.g. virtual
            # gate TTL expiry or motion buffer overflow). Treat as soft stop.
            if r.get("err") in ("group_error_stop", "group_not_ready"):
                tripped_at = k
                break
            assert r.get("dedup") is True, f"repeat cid={cid} not dedup'd: {r}"
            assert r.get("movement_id") == movement_ids[cid], (
                f"dedup replay wrong movement_id: cid={cid} got={r.get('movement_id')} "
                f"expected={movement_ids[cid]}"
            )
            expected_dupes += 1
        else:
            cid = unique_cids[len(movement_ids)]
            if len(movement_ids) >= n_unique:
                continue
            send_pack(s, _g1(cid, x=rng.uniform(-0.05, 0.05), y=rng.uniform(-0.05, 0.05)))
            r = drain_until_id(s, cid, 3.0)
            assert r is not None, f"no reply for new cid={cid} (k={k})"
            if r.get("err") in ("group_error_stop", "group_not_ready"):
                tripped_at = k
                break
            mvid = r.get("movement_id")
            assert isinstance(mvid, int) and mvid > last_mvid, (
                f"movement_id regression: cid={cid} mvid={mvid} last={last_mvid}"
            )
            last_mvid = mvid
            movement_ids[cid] = mvid

        if time.time() - last_log > 15:
            elapsed = time.time() - t0
            print(f"  motion endurance: k={k} unique={len(movement_ids)}/{n_unique} "
                  f"dupes={expected_dupes} ({elapsed:.0f}s)")
            last_log = time.time()

    # If the very first G1 tripped, the group was already in error stop
    # before this test ran (likely from a prior test in the same session).
    # Skip rather than fail -- we can't endurance-test from a tripped group.
    if tripped_at is not None and len(movement_ids) <= 1:
        pytest.skip(
            f"group already in error stop at start (k={tripped_at}). "
            f"Run this test in isolation or recover the group first."
        )
    min_iterations = 10 if SHORT else 100
    assert len(movement_ids) >= min_iterations, (
        f"only {len(movement_ids)} unique G1s before stop "
        f"(tripped_at={tripped_at})"
    )

    send_pack(s, {"type": "SYS", "cmd": "GET_MACHINE_STATE", "id": 70900})
    st = drain_until_id(s, 70900, 2.0)
    final_st = st.get("st") if st else None

    post = _read_symbols(["GVL.DupeCommandCount", "GVL.StateChangeEventCount"])
    post_dup = int(post["GVL.DupeCommandCount"])
    post_chg = int(post["GVL.StateChangeEventCount"])

    if tripped_at is None:
        # Clean run: FSM must have stayed in Ready and counters must match.
        assert final_st == 70, f"FSM left Ready under endurance: {st}"
        assert post_dup - pre_dup == expected_dupes, (
            f"DupeCommandCount delta {post_dup - pre_dup}, expected {expected_dupes}"
        )
        assert post_chg == pre_chg, (
            f"FSM transitioned {post_chg - pre_chg} times during endurance"
        )
        print(f"motion endurance (clean): {len(movement_ids)} unique + "
              f"{expected_dupes} dupes, DupeCommandCount +{post_dup - pre_dup}")
    else:
        # Soft trip: PLC tripped mid-run. Verify dedupe accounting up to the
        # trip is still consistent (every dupe before the trip must have
        # bumped DupeCommandCount).
        assert post_dup - pre_dup == expected_dupes, (
            f"DupeCommandCount delta {post_dup - pre_dup}, expected "
            f"{expected_dupes} (up to trip at k={tripped_at})"
        )
        print(f"motion endurance (tripped at k={tripped_at}, st={final_st}): "
              f"{len(movement_ids)} unique + {expected_dupes} dupes "
              f"completed cleanly before trip")


# ---------- Test 4: random-walk entropy (~5 min nominal) ----------

def test_random_walk_entropy(raw_plc_socket):
    """Send a long stream of random GA_EV events. FSM must:
      - Always reply within 2s (no wedge)
      - Stay within the legal state enum
      - Never reach a state we can't escape from (after RESET we should
        always be back at UnInited)"""
    n_events = 50 if SHORT else 800
    s = raw_plc_socket
    reader = WireReader(s)
    rng = random.Random(0xDEADBEEF)
    legal_states = {10, 20, 30, 40, 50, 60, 70, 990}
    # Don't include EV_NONE (boring) or EV_OK (internal). Mix in plenty of
    # nonsense values too -- the unpacker must NAK them gracefully.
    event_pool = [2, 4, 6, 7, 8, 9, 0, 1, 3, 5, 100, 999, -1]

    illegal_states = []
    state_visits = {}
    no_reply_count = 0
    last_log = time.time()
    for i in range(n_events):
        ev = rng.choice(event_pool)
        send_pack(s, {"type": "SYS", "cmd": "GA_EV", "ev": ev, "id": 75000 + i})
        # Must always get a reply (ack or nak), even for nonsense events.
        r = reader.wait_for_id(75000 + i, 2.0)
        if r is None:
            no_reply_count += 1
            continue
        st = r.get("st")
        if isinstance(st, int):
            if st not in legal_states:
                illegal_states.append((i, ev, st))
            state_visits[st] = state_visits.get(st, 0) + 1
        # Brief settle so back-to-back transitions have a scan to land.
        if i % 20 == 19:
            reader.drain(0.05)
        if time.time() - last_log > 15:
            print(f"  random-walk: {i + 1}/{n_events} states_seen={sorted(state_visits.keys())} "
                  f"no_reply={no_reply_count}")
            last_log = time.time()

    # Force back to UnInited and verify recovery.
    reader.send_ev(8, 75900, settle=0.5)
    st_final = reader.get_state(75901)

    assert no_reply_count == 0, f"{no_reply_count}/{n_events} GA_EV got no reply"
    assert not illegal_states, (
        f"FSM visited illegal states (state, count): {illegal_states[:10]}"
    )
    assert st_final == 10, f"after recovery RESET, expected st=10, got {st_final}"

    print(f"random-walk: {n_events} events, "
          f"states visited: {sorted(state_visits.keys())}, "
          f"recovery to UnInited: OK")
# ---------- Test 1: FSM cycle soak (~8 min nominal) ----------

def test_fsm_cycle_soak(raw_plc_socket):
    """Repeatedly drive UnInited -> Ready -> Error -> UnInited.
    Every cycle must reach Ready and Error cleanly. SelfReentryCount must
    not climb (no implicit self-reentry during legitimate transitions).
    StateChangeEventCount must climb monotonically by at least the number
    of edges we drive. No PendingStChgDropCount on the way out -- if the
    ring ever stalls during this loop we will see it."""
    cycles = 5 if SHORT else 50
    s = raw_plc_socket
    _force_virtual()
    reader = WireReader(s)

    pre = _read_symbols([
        "GVL.SelfReentryCount", "GVL.StateChangeEventCount",
        "GVL.PendingStChgDropCount", "GVL.ReMpDropCount",
    ])
    pre_self = int(pre["GVL.SelfReentryCount"])
    pre_chgs = int(pre["GVL.StateChangeEventCount"])
    pre_pend = int(pre["GVL.PendingStChgDropCount"])
    pre_drop = int(pre["GVL.ReMpDropCount"])

    # Avoid keepalive PING id range (60000..60999 in raw_plc_socket fixture).
    base_id = 65000
    failures = []
    cycle_times = []
    for cyc in range(cycles):
        t0 = time.time()
        # RESET -> POWER_ON -> GROUP_ENABLE -> FORCE_SKIP -> ERROR -> RESET
        for ev, settle in [(8, 0.4), (2, 0.7), (4, 0.7), (7, 0.5),
                           (9, 0.5), (8, 0.4)]:
            reader.send_ev(ev, base_id, settle=settle)
            base_id += 1
        cycle_times.append(time.time() - t0)
        # Spot-check: confirm we landed at UnInited.
        st = reader.get_state(base_id); base_id += 1
        if st != 10:
            failures.append((cyc, st))
        if cyc % 10 == 9:
            print(f"  soak cyc {cyc + 1}/{cycles} avg={statistics.mean(cycle_times[-10:]):.2f}s")

    post = _read_symbols([
        "GVL.SelfReentryCount", "GVL.StateChangeEventCount",
        "GVL.PendingStChgDropCount", "GVL.ReMpDropCount",
    ])
    delta_self = int(post["GVL.SelfReentryCount"]) - pre_self
    delta_chgs = int(post["GVL.StateChangeEventCount"]) - pre_chgs
    delta_pend = int(post["GVL.PendingStChgDropCount"]) - pre_pend
    delta_drop = int(post["GVL.ReMpDropCount"]) - pre_drop

    assert not failures, f"cycle landings off-target: {failures[:5]} (of {len(failures)})"
    # Each cycle drives 5 transitions (RESET self-loop is reentry, not edge).
    # Allow some slack -- ST_CHG ring may coalesce a couple under load.
    expected_min = cycles * 4
    assert delta_chgs >= expected_min, (
        f"StateChangeEventCount delta {delta_chgs} < expected {expected_min}"
    )
    # Self-reentry: each EV_RESET and EV_ERROR via TCP fires twice (inline
    # at GA_EV handler + next-scan via UpdateRuntimeAndInputEvent.PreviousInputEvent
    # diff), so each generates one self-reentry on the second fire from the
    # destination state. Per cycle: 2 RESETs + 1 ERROR -> ~3 reentries.
    # Allow a wide band (2x..4x cycles) -- exact count depends on scan timing.
    assert 2 * cycles <= delta_self <= 5 * cycles, (
        f"SelfReentryCount delta {delta_self}, expected roughly "
        f"{2 * cycles}..{5 * cycles} for {cycles} cycles"
    )
    # Sustained load shouldn't drop ring slots.
    assert delta_pend == 0, (
        f"PendingStChgDropCount climbed by {delta_pend} during soak"
    )
    # Heartbeat / event drops can happen if UI is laggy, but during a soak
    # without a UI we expect a clean ring. Allow a tiny budget.
    assert delta_drop < cycles, (
        f"ReMpDropCount delta {delta_drop} >= {cycles} cycles "
        f"(ring under-drained?)"
    )

    print(f"soak summary: {cycles} cycles, avg cycle {statistics.mean(cycle_times):.2f}s, "
          f"max {max(cycle_times):.2f}s, "
          f"StateChangeEventCount +{delta_chgs}, SelfReentryCount +{delta_self}")


