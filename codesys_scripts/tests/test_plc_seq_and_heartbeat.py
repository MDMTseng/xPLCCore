# Regression tests for the global packet-seq stamp and periodic HEARTBEAT
# push event added 2026-04-28.
#
# Why seq:
#   Per-request `id` echo lets the UI detect lost replies via timeout, but
#   unsolicited events (ST_CHG, MOVE_DONE, COORD_SET, HEARTBEAT) have no
#   `id`. Without seq, a ring-drop of an event leaves no trace beyond a
#   bumped GVL.ReMpDropCount -- the UI sees "something was dropped" but
#   not which event. With a global monotonic seq stamped by
#   MsgPakInfoWrapup on every emission, the UI can detect gaps regardless
#   of packet kind.
#
# Why HEARTBEAT:
#   Lets the UI detect a dead PLC socket without sending its own SYS/PING,
#   and gives seq-gap detection a regular cadence so the "expected next
#   seq" advances even during idle stretches.
import time
from pathlib import Path

import test_movement_sequence as tms
from tests.test_plc_holes_e2e import _read_symbols


def test_seq_counter_monotonic_and_growing():
    """Seq must climb on its own (HEARTBEAT alone guarantees ~2/sec) and
    must never go backwards. A regression that resets the counter on
    every Wrapup, or removes the increment, would surface here."""
    a = _read_symbols(["GVL.ReMpSeqCounter"])
    seq_a = int(a["GVL.ReMpSeqCounter"])
    time.sleep(2.0)
    b = _read_symbols(["GVL.ReMpSeqCounter"])
    seq_b = int(b["GVL.ReMpSeqCounter"])
    assert seq_b > seq_a, (
        f"ReMpSeqCounter did not advance in 2s ({seq_a} -> {seq_b}); "
        f"HEARTBEAT may not be firing or seq stamp regressed"
    )
    # 2s should give >= 3 heartbeats (interval=500ms) plus reply traffic.
    # Be generous on the lower bound to avoid flakes from slow reads.
    assert (seq_b - seq_a) >= 3, (
        f"Seq advanced only {seq_b - seq_a} in 2s; expected >=3 from "
        f"HEARTBEAT alone"
    )


def test_seq_appears_on_sys_reply(fsm_ready):
    """Every reply must carry a `seq` field. PING is the cheapest probe."""
    r = tms.harness_send({"type": "SYS", "cmd": "PING"}, timeout=2.0)
    assert "seq" in r, (
        f"PING reply missing `seq` field; Wrapup did not stamp it. "
        f"Reply keys: {sorted(r.keys())}"
    )
    assert isinstance(r["seq"], int) and r["seq"] > 0, (
        f"PING reply seq is not a positive int: {r['seq']!r}"
    )


def test_seq_strictly_increases_across_replies(fsm_ready):
    """Two back-to-back PINGs must have strictly-increasing seq. A
    regression that stamped the same value (e.g. forgot to increment, or
    incremented after pack) would tie them."""
    r1 = tms.harness_send({"type": "SYS", "cmd": "PING"}, timeout=2.0)
    r2 = tms.harness_send({"type": "SYS", "cmd": "PING"}, timeout=2.0)
    s1, s2 = r1.get("seq"), r2.get("seq")
    assert s1 is not None and s2 is not None, (
        f"PING replies missing seq: {s1!r}, {s2!r}"
    )
    assert s2 > s1, f"Seq did not advance between PINGs: {s1} -> {s2}"


def test_heartbeat_periodic_via_last_stamp():
    """LastHeartbeatMs is rewritten on every heartbeat emit. Sample it,
    wait one interval-plus-margin, sample again -- it must have moved.
    Going through online-read instead of the event stream because the
    Python harness is request/reply-only; the actual event delivery to
    the renderer's TCP socket is exercised by the UI integration tests.
    """
    a = _read_symbols(["AxisGroupSM.LastHeartbeatMs", "AxisGroupSM.HEARTBEAT_INTERVAL_MS"])
    interval = int(a["AxisGroupSM.HEARTBEAT_INTERVAL_MS"])
    assert interval > 0 and interval <= 2000, (
        f"HEARTBEAT_INTERVAL_MS={interval} outside sane range [1,2000]"
    )
    last_a = int(a["AxisGroupSM.LastHeartbeatMs"])
    # Wait two intervals -- guarantees at least one tick crossed even
    # if our first sample landed mid-interval.
    time.sleep((interval / 1000.0) * 2 + 0.2)
    b = _read_symbols(["AxisGroupSM.LastHeartbeatMs"])
    last_b = int(b["AxisGroupSM.LastHeartbeatMs"])
    assert last_b > last_a, (
        f"LastHeartbeatMs did not advance ({last_a} -> {last_b}) over "
        f"{2*interval}ms; periodic HEARTBEAT block is wedged"
    )


def test_heartbeat_drives_seq_during_idle():
    """Even with no harness traffic, seq must keep climbing because
    HEARTBEAT alone bumps it. This is the property the UI relies on for
    seq-gap detection during idle stretches."""
    a = _read_symbols(["GVL.ReMpSeqCounter", "AxisGroupSM.HEARTBEAT_INTERVAL_MS"])
    interval = int(a["AxisGroupSM.HEARTBEAT_INTERVAL_MS"])
    seq_a = int(a["GVL.ReMpSeqCounter"])
    # Sleep 3 intervals; expect at least 2 heartbeat-driven bumps even
    # if other event sources are quiet.
    time.sleep((interval / 1000.0) * 3 + 0.2)
    b = _read_symbols(["GVL.ReMpSeqCounter"])
    seq_b = int(b["GVL.ReMpSeqCounter"])
    delta = seq_b - seq_a
    assert delta >= 2, (
        f"Seq advanced only {delta} over {3*interval}ms idle; HEARTBEAT "
        f"is not driving the counter"
    )
