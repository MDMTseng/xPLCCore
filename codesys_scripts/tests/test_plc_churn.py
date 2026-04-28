# Connection churn / reset state-machine stress.
#
# Repeatedly connect/disconnect to PLC TCP 8125 and verify each fresh
# socket gets a pong within tight latency. Catches:
#   - listener slot release race (xResetConnection sticking TRUE)
#   - fbDataBuffer.clear() not running on reset
#   - fbMsgPakParser.ResetParser() leaving stale stack-level state
#   - 20ms grace timer + reset state machine deadlock
#
# The PLC reset state machine (FB_TcpMsgPakServer.st phases 1->2->3) takes
# ~20ms grace + a few ms for TCP teardown. We verify the second socket can
# pong within 1.5s -- generous to absorb Windows TCP timer jitter.
import socket
import time

import msgpack
import pytest

from tests._raw_plc import ui_set_tcp, PLC_HOST, PLC_PORT


CHURN_ITERATIONS = 30


def _connect_and_ping(ping_id: int, deadline_s: float = 1.5) -> float | None:
    """Open a fresh socket, send one PING, wait for the matching pong.
    Returns elapsed seconds on success, None on failure."""
    t0 = time.time()
    try:
        s = socket.socket(); s.settimeout(deadline_s)
        s.connect((PLC_HOST, PLC_PORT))
    except OSError:
        return None
    try:
        s.sendall(msgpack.packb(
            {"type": "SYS", "cmd": "PING", "id": ping_id},
            use_bin_type=True,
        ))
        unp = msgpack.Unpacker(raw=False, strict_map_key=False)
        s.settimeout(0.3)
        end = t0 + deadline_s
        while time.time() < end:
            try:
                chunk = s.recv(4096)
            except (socket.timeout, OSError):
                continue
            if not chunk:
                return None
            try:
                unp.feed(chunk)
                for obj in unp:
                    if isinstance(obj, dict) and obj.get("id") == ping_id and obj.get("pong"):
                        return time.time() - t0
            except Exception:
                continue
        return None
    finally:
        try: s.close()
        except OSError: pass


@pytest.fixture
def ui_disconnected():
    ui_set_tcp(False)
    time.sleep(0.5)
    yield
    time.sleep(0.3)
    ui_set_tcp(True)
    time.sleep(0.5)


def test_rapid_connect_disconnect_30x(ui_disconnected):
    """30 connect/PING/disconnect cycles. Every iteration must pong.
    Track latency to surface degradation (a leak in the reset path
    would show as monotonically growing latency)."""
    latencies = []
    failures = []
    for i in range(CHURN_ITERATIONS):
        elapsed = _connect_and_ping(95000 + i)
        if elapsed is None:
            failures.append(i)
        else:
            latencies.append(elapsed)
        # Tight churn: don't sleep between iterations beyond what TCP needs.
        time.sleep(0.15)

    assert not failures, (
        f"connect/PING failed on iterations {failures} of {CHURN_ITERATIONS}"
    )
    # Sanity: median latency should be well under 1s; max under 1.5s.
    latencies.sort()
    median = latencies[len(latencies) // 2]
    p95 = latencies[int(len(latencies) * 0.95)]
    assert median < 1.0, f"median connect-to-pong latency {median:.2f}s > 1s"
    assert p95 < 1.5, f"p95 connect-to-pong latency {p95:.2f}s > 1.5s"

    # Drift check: only meaningful if latencies enter the 100ms+ band.
    # Sub-30ms ratios are TCP/scheduler noise, not real leaks.
    early = sum(latencies[:5]) / 5 if len(latencies) >= 10 else None
    late = sum(latencies[-5:]) / 5 if len(latencies) >= 10 else None
    if early is not None and late is not None and early > 0 and late > 0.3:
        assert late / early < 3.0, (
            f"latency drift: early avg {early:.3f}s, late avg {late:.3f}s "
            f"(ratio {late/early:.2f}x) -- leak in reset state machine?"
        )


def test_abrupt_disconnect_releases_slot(ui_disconnected):
    """RST the socket without graceful close, verify the next connect
    succeeds within 8s (need to clear the IDLE_TIMEOUT margin if SO_RST
    isn't observed cleanly). PLC's xLastConnectionActive edge-detect
    should pick up the drop and reset listener."""
    for i in range(5):
        s = socket.socket()
        # SO_LINGER 0 forces RST on close instead of FIN
        s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                     b"\x01\x00\x00\x00\x00\x00\x00\x00")
        s.settimeout(2.0)
        try:
            s.connect((PLC_HOST, PLC_PORT))
            s.sendall(msgpack.packb({"type": "SYS", "cmd": "PING", "id": 96000 + i},
                                    use_bin_type=True))
            time.sleep(0.05)
            s.close()  # RST due to SO_LINGER 0
        except OSError:
            try: s.close()
            except OSError: pass

        time.sleep(0.3)  # let PLC observe the drop + reset
        elapsed = _connect_and_ping(96100 + i, deadline_s=8.0)
        assert elapsed is not None, (
            f"after RST iteration {i}, fresh connect could not pong in 8s"
        )


def test_partial_write_then_disconnect(ui_disconnected):
    """Send only the first byte of a PING then close the socket. Parser
    should be left mid-object; the next connection's PING must still be
    served correctly (i.e. PLC's reset path must scrub parser state)."""
    for i in range(3):
        s = socket.socket(); s.settimeout(2.0)
        s.connect((PLC_HOST, PLC_PORT))
        # Send just the map-header byte
        try:
            s.sendall(b"\x84")  # map of 4 elements -- parser will wait for body
            time.sleep(0.05)
        finally:
            s.close()
        time.sleep(0.3)

        # Fresh socket must succeed
        elapsed = _connect_and_ping(97000 + i, deadline_s=8.0)
        assert elapsed is not None, \
            f"after partial-write iteration {i}, fresh PING failed"
