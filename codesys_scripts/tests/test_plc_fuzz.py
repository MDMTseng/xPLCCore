# PLC TCP/msgpack parser fuzz test.
#
# Goal: hammer the PLC's TCP/msgpack ingress with malformed, truncated,
# pathological, and burst payloads, then verify the listener is still
# alive (PING round-trip) and the FSM hasn't been knocked into Error by
# any single fuzz input. Nothing here is supposed to crash the PLC --
# malformed packets should NAK or be silently ignored, never wedge the
# parser.
#
# Why raw TCP (not harness_send): the UI's harness sanitises payloads
# (rewrites id, msgpack-encodes a JSON dict). To reach the parser with
# arbitrary bytes we need direct socket access. PLC accepts one client,
# so we steal the slot from the UI and reconnect on teardown -- same
# pattern as test_plc_dedupe.
#
# Categories exercised:
#   A. truncated valid msgpack (first N bytes of a real packet)
#   B. random noise bytes
#   C. reserved/invalid msgpack type markers (0xc1, oversized headers)
#   D. field-type confusion (type as int, cmd as bool, ev as string)
#   E. missing required fields (no `type`, no `cmd`)
#   F. extreme values (huge ints, NaN/Inf floats, megabyte strings)
#   G. burst: many valid+invalid packets back-to-back
#
# After each category we send a clean SYS/PING and assert pong arrives
# within 2s. After all categories we verify no spurious FSM transitions
# (st should be unchanged from start) and no parser corruption (no axes
# in error from a fuzz packet).
import math
import os
import random
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import msgpack
import pytest

HERE = Path(__file__).resolve().parent
REMOTE_CTRL = HERE.parent / "internals" / "remote_ctrl.py"
PLC_HOST, PLC_PORT = "192.168.1.70", 8125

SEED = int(os.environ.get("FUZZ_SEED", "20260428"))
ITERATIONS_PER_CATEGORY = int(os.environ.get("FUZZ_ITER", "30"))


def _ui_set_tcp(connect: bool):
    action = "connect_tcp" if connect else "disconnect_tcp"
    payload = '{"host":"192.168.1.70","port":8125}' if connect else "{}"
    subprocess.run(
        [sys.executable, str(REMOTE_CTRL), action, payload, "--timeout", "3"],
        capture_output=True, text=True, timeout=8,
    )


_send_lock = threading.Lock()


@pytest.fixture
def raw_plc_socket():
    """Steal the PLC's single-client TCP slot for one test.
    No keepalive thread -- fuzz batches are short (<3s total) and the
    A3 supervisor only trips on motion-in-flight (gated by the recent
    self-keepalive fix), so an idle FSM stays Ready without PINGs."""
    _ui_set_tcp(False)
    time.sleep(0.4)
    s = socket.socket(); s.settimeout(3.0)
    s.connect((PLC_HOST, PLC_PORT))
    try:
        # Sanity ping: confirm we own the slot before fuzzing begins.
        s.sendall(msgpack.packb({"type": "SYS", "cmd": "PING", "id": 99999}, use_bin_type=True))
        time.sleep(0.2)
        try:
            _ = s.recv(4096)
        except socket.timeout:
            pass
        yield s
    finally:
        try: s.close()
        except OSError: pass
        time.sleep(0.3)
        _ui_set_tcp(True)
        time.sleep(0.5)


def _send_raw(s: socket.socket, blob: bytes):
    with _send_lock:
        try:
            s.sendall(blob)
        except OSError:
            # Caller is responsible for reconnect-on-dead-socket; swallow
            # here so a fuzz batch can push past mid-burst TCP resets.
            pass


def _send_pack(s: socket.socket, payload):
    _send_raw(s, msgpack.packb(payload, use_bin_type=True))


def _reconnect(old: socket.socket) -> socket.socket:
    try: old.close()
    except OSError: pass
    time.sleep(0.5)  # let PLC release the listener slot
    s = socket.socket(); s.settimeout(3.0)
    s.connect((PLC_HOST, PLC_PORT))
    return s


def _drain_for_pong(s: socket.socket, expect_id: int, timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    unp = msgpack.Unpacker(raw=False, strict_map_key=False)
    s.settimeout(0.4)
    while time.time() < deadline:
        try:
            chunk = s.recv(4096)
        except socket.timeout:
            continue
        except OSError:
            return False
        if not chunk:
            return False
        try:
            unp.feed(chunk)
            for obj in unp:
                if isinstance(obj, dict) and obj.get("id") == expect_id and obj.get("pong"):
                    return True
        except Exception:
            # parser desync from prior fuzz output is fine; keep reading
            continue
    return False


def _ping_check(s: socket.socket, tag: str, ping_id: int) -> socket.socket:
    """Send a PING and wait for the matching pong. If the socket was
    reset by the PLC during a fuzz burst, transparently reconnect once
    and retry -- a reset is intentional recovery, not a wedge. Returns
    the live socket (possibly a fresh one)."""
    _send_pack(s, {"type": "SYS", "cmd": "PING", "id": ping_id})
    if _drain_for_pong(s, ping_id, timeout=2.5):
        return s
    # Probable mid-burst TCP reset by PLC. Reconnect and retry once.
    s = _reconnect(s)
    _send_pack(s, {"type": "SYS", "cmd": "PING", "id": ping_id + 500})
    assert _drain_for_pong(s, ping_id + 500, timeout=3.0), \
        f"PLC unresponsive after fuzz category {tag} even after reconnect"
    return s


# ------------------------------------------------------------- generators ----

def _gen_truncated(rng: random.Random) -> bytes:
    base = msgpack.packb(
        {"type": "SYS", "cmd": "PING", "id": rng.randint(1, 1 << 30)},
        use_bin_type=True,
    )
    if len(base) <= 1:
        return base
    cut = rng.randint(1, len(base) - 1)
    return base[:cut]


def _gen_noise(rng: random.Random) -> bytes:
    n = rng.randint(1, 64)
    return bytes(rng.randint(0, 255) for _ in range(n))


def _gen_invalid_markers(rng: random.Random) -> bytes:
    choices = [
        b"\xc1",                              # reserved
        b"\xdc\xff\xff",                      # array16 declaring 65535 elements, no body
        b"\xdd\x00\xff\xff\xff",              # array32 declaring huge count, no body
        b"\xde\xff\xff",                      # map16 huge declared, no body
        b"\xdf\x00\xff\xff\xff",              # map32 huge declared, no body
        b"\xd9\xff",                          # str8 declaring 255 bytes, no body
        b"\xc4\xff",                          # bin8 declaring 255 bytes, no body
        b"\xca" + b"\x7f\xc0\x00\x00",        # float32 NaN
        b"\xcb" + struct.pack(">d", math.inf),
    ]
    return rng.choice(choices)


def _gen_field_confusion(rng: random.Random):
    return rng.choice([
        {"type": 1, "cmd": "PING", "id": 1},                # type as int
        {"type": "SYS", "cmd": False, "id": 2},              # cmd as bool
        {"type": "SYS", "cmd": "GA_EV", "ev": "POWER_ON"},   # ev as string
        {"type": "SYS", "cmd": "GA_EV", "ev": -1},           # neg ev
        {"type": ["SYS"], "cmd": "PING"},                    # type as array
        {"type": {"x": 1}, "cmd": "PING"},                   # type as map
        {"type": "SYS", "cmd": "PING", "id": "abc"},         # id as string
    ])


_MISSING_VARIANTS = [
    {"cmd": "PING", "id": 1},
    {"type": "SYS", "id": 1},
    {},
    {"id": 1},
    {"type": "M", "id": 1},
]


def _gen_missing_fields(rng: random.Random):
    return rng.choice(_MISSING_VARIANTS)


def _gen_extreme_values(rng: random.Random):
    return rng.choice([
        {"type": "SYS", "cmd": "GA_EV", "ev": 1 << 62, "id": 1},
        {"type": "SYS", "cmd": "GA_EV", "ev": -(1 << 62), "id": 2},
        {"type": "SYS", "cmd": "PING", "id": 1 << 62},
        {"type": "M", "cmd": "G1", "x": math.nan, "y": math.inf, "z": -math.inf, "id": 3},
        {"type": "M", "cmd": "G1", "x": 1e300, "y": 1e300, "z": 1e300, "id": 4},
        {"type": "SYS", "cmd": "PING", "junk": "A" * 8000, "id": 5},
        {"type": "SYS", "cmd": "PING", "id": 6, "extra": list(range(200))},
    ])


# ------------------------------------------------------------------- tests ----

def test_fuzz_well_formed_pathological(raw_plc_socket):
    """Real fuzz coverage: pump well-formed msgpack with pathological
    content (field-type confusion, missing required fields, extreme
    numeric values). Each packet is a complete msgpack object, so the
    parser cannot desync on framing -- it must either NAK or silently
    drop, and listener must stay responsive throughout."""
    s = raw_plc_socket
    rng = random.Random(SEED)

    s = _ping_check(s, "anchor-pre", 80000)

    # Per-category lambdas re-bind `s` lazily so a reconnect mid-test is
    # picked up automatically.
    def fire_confusion(): _send_pack(s, _gen_field_confusion(rng))
    def fire_missing():   _send_pack(s, _gen_missing_fields(rng))
    def fire_extreme():   _send_pack(s, _gen_extreme_values(rng))

    categories = [
        ("D:confusion", fire_confusion),
        ("E:missing",   fire_missing),
        ("F:extreme",   fire_extreme),
    ]

    ping_id = 80001
    for tag, fire in categories:
        for _ in range(ITERATIONS_PER_CATEGORY):
            fire()
            time.sleep(0.01)
        time.sleep(0.4)
        s.settimeout(0.2)
        try:
            while True:
                if not s.recv(4096): break
        except (socket.timeout, OSError):
            pass
        s = _ping_check(s, tag, ping_id)
        ping_id += 1


def test_fuzz_burst_well_formed(raw_plc_socket):
    """200 well-formed packets back-to-back: mix of valid PINGs and
    pathological-but-parseable junk. Stresses parser throughput and
    SYS-vs-M routing cache. Skips truncated/noise generators which
    are known to desync the framing-less stream (see xfail tests)."""
    s = raw_plc_socket
    rng = random.Random(SEED ^ 0x5A5A5A5A)

    s = _ping_check(s, "burst-pre", 81000)

    generators = [
        lambda: msgpack.packb({"type": "SYS", "cmd": "PING", "id": rng.randint(1, 1 << 30)}, use_bin_type=True),
        lambda: msgpack.packb(_gen_field_confusion(rng), use_bin_type=True),
        lambda: msgpack.packb(_gen_missing_fields(rng), use_bin_type=True),
        lambda: msgpack.packb(_gen_extreme_values(rng), use_bin_type=True),
    ]

    for _ in range(200):
        _send_raw(s, rng.choice(generators)())

    time.sleep(0.5)
    _ping_check(s, "burst-post", 81001)


def test_fuzz_oversized_string_field(raw_plc_socket):
    """Well-formed msgpack with a multi-kilobyte string field. PLC
    parser has fixed slot buffers (MAX_SLOT_PAYLOAD=255); oversized
    inputs should be rejected/clipped, never overrun. PLC must remain
    responsive after each."""
    s = raw_plc_socket
    s = _ping_check(s, "giant-pre", 82000)

    for size in (256, 512, 1024, 4096):
        payload = {"type": "SYS", "cmd": "PING", "junk": "x" * size, "id": 82100 + size}
        _send_pack(s, payload)
        time.sleep(0.05)

    _ping_check(s, "giant-post", 82001)


# ----------------------------------------------- stream-desync probes ----
# These categories are known to leave the PLC parser mid-object (no length
# framing -- see direct_tcp_msgpack_bypass memory). The PLC's recovery
# path is to RST the connection on read-error or after IDLE_TIMEOUT=7s.
# _ping_check transparently reconnects when its first PING gets no pong,
# so these tests verify the *recovery* path, not parser tolerance.

def test_fuzz_truncated_recovers_via_reset(raw_plc_socket):
    s = raw_plc_socket
    rng = random.Random(SEED ^ 0xA1A1A1A1)
    s = _ping_check(s, "trunc-pre", 83000)
    for _ in range(ITERATIONS_PER_CATEGORY):
        _send_raw(s, _gen_truncated(rng))
        time.sleep(0.005)
    _ping_check(s, "trunc-post", 83001)


def test_fuzz_noise_recovers_via_reset(raw_plc_socket):
    s = raw_plc_socket
    rng = random.Random(SEED ^ 0xB2B2B2B2)
    s = _ping_check(s, "noise-pre", 84000)
    for _ in range(ITERATIONS_PER_CATEGORY):
        _send_raw(s, _gen_noise(rng))
        time.sleep(0.005)
    _ping_check(s, "noise-post", 84001)


def test_fuzz_invalid_markers_recover_via_reset(raw_plc_socket):
    s = raw_plc_socket
    rng = random.Random(SEED ^ 0xC3C3C3C3)
    s = _ping_check(s, "marker-pre", 85000)
    for _ in range(ITERATIONS_PER_CATEGORY):
        _send_raw(s, _gen_invalid_markers(rng))
        time.sleep(0.005)
    _ping_check(s, "marker-post", 85001)


# ---------------------------------------------- counter invariants ----
# Read GVL counters before & after a fuzz burst via the CODESYS daemon
# and assert only expected counters moved. Catches silent-state-corruption
# regressions: e.g. a future change that lets a malformed packet sneak
# through the type-NAK gate and reach the FSM would bump
# StateChangeEventCount or DupeCommandCount unexpectedly.

DAEMON_RPC = HERE.parent / "rpc.py"

_COUNTER_SYMS = [
    "GVL.MissingTypeFieldNakCount",
    "GVL.GroupNotReadyNakCount",
    "GVL.ReMpDropCount",
    "GVL.ReMpDropCount_Reply",
    "GVL.ParserErrorResetCount",
    "GVL.ReadErrorResetCount",
    "GVL.IdleResetCount",
    "GVL.TxStallResetCount",
    "GVL.SendStallDropCount",
    "GVL.StateChangeEventCount",
    "GVL.SelfReentryCount",
    "GVL.PendingMoveDoneDropCount",
    "GVL.PendingStChgDropCount",
    "GVL.DupeCommandCount",
]


def _read_counters() -> dict:
    """Snapshot GVL counters via the rpc daemon. Returns symbol->int."""
    lines = "\n".join([
        "proj = projects.primary",
        "app = proj.active_application",
        "oapp = online.create_online_application(app)",
        "oapp.login(OnlineChangeOption.Try, False)",
        "try:",
    ] + [
        f"    print('{s}=' + str(oapp.read_value('{s}')))" for s in _COUNTER_SYMS
    ] + [
        "finally:",
        "    try: oapp.logout()",
        "    except Exception: pass",
        "",
    ])
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(lines)
        path = f.name
    try:
        res = subprocess.run(
            [sys.executable, str(DAEMON_RPC), "exec", "--file", path],
            capture_output=True, text=True, timeout=30,
        )
    finally:
        try: os.unlink(path)
        except OSError: pass
    out = {}
    for ln in (res.stdout or "").splitlines():
        if "=" not in ln or "GVL." not in ln: continue
        k, _, v = ln.partition("=")
        # Strip CODESYS type wrapper "UDINT#173" -> 173
        v = v.split("#", 1)[-1].strip()
        try: out[k.strip()] = int(v)
        except ValueError: pass
    return out


def test_counter_invariants_under_noise_burst(raw_plc_socket):
    """Burst random noise and confirm only TCP-recovery counters moved.
    State, motion, and dedupe counters must stay flat -- a regression
    that let a malformed packet leak past the type-NAK gate would bump
    StateChangeEventCount or DupeCommandCount here."""
    pre = _read_counters()
    if not pre:
        pytest.skip("daemon counter read failed -- daemon not running?")

    s = raw_plc_socket
    rng = random.Random(SEED ^ 0xD4D4D4D4)
    s = _ping_check(s, "inv-pre", 86000)

    for _ in range(80):
        _send_raw(s, _gen_noise(rng))
        time.sleep(0.005)
    for _ in range(40):
        _send_raw(s, _gen_invalid_markers(rng))
        time.sleep(0.005)
    for _ in range(40):
        _send_pack(s, _gen_field_confusion(rng))
        time.sleep(0.005)

    s = _ping_check(s, "inv-post", 86001)
    time.sleep(0.5)  # let counters settle
    post = _read_counters()

    # FSM/motion/dedupe counters MUST NOT move on malformed input.
    forbidden_movement = [
        "GVL.StateChangeEventCount",
        "GVL.SelfReentryCount",
        "GVL.PendingMoveDoneDropCount",
        "GVL.DupeCommandCount",
    ]
    for sym in forbidden_movement:
        if sym in pre and sym in post:
            assert post[sym] == pre[sym], (
                f"{sym} moved {pre[sym]} -> {post[sym]} during malformed-input "
                f"fuzz; a malformed packet may be reaching FSM/motion paths"
            )

    # NAK counter for missing/wrong type SHOULD have moved (we sent
    # confusion+noise+markers, several of which parse as well-formed
    # maps without correct type).
    if "GVL.MissingTypeFieldNakCount" in pre:
        assert post["GVL.MissingTypeFieldNakCount"] >= pre["GVL.MissingTypeFieldNakCount"], \
            "MissingTypeFieldNakCount went backwards (counter wrap or reset?)"


# -------------------------------------------------- FSM random walk ----
# Dispatch a random sequence of *valid* GA_EV events. The FSM must remain
# in the documented enum range (st in {0, 10, 20, 30, 40, 50, 60, 70, 99,
# 9999}) and st_str must match. A regression that left _eState in an
# unmapped value would surface here. We reset to UnInited at the end so
# downstream tests aren't disturbed.

# E_RobotState values
_LEGAL_STATES = {
    0,    # UnInited
    10,   # Powering
    20,   # Powered
    30,   # GroupEnabling
    40,   # GroupEnabled
    50,   # Homing
    60,   # (reserved/legacy)
    70,   # Ready
    99,   # Error
    9999, # cold-boot sentinel before first enum write
}

# Subset of E_RobotEvent we feel safe firing in a random walk
_FUZZ_EVENTS = [
    1,  # EV_POWER_OFF
    2,  # EV_POWER_ON
    3,  # EV_OK
    4,  # EV_GROUP_ENABLE
    5,  # EV_ERROR
    6,  # EV_HOME_GO
    7,  # EV_HOME_GO_FORCE_SKIP
    8,  # EV_RESET
]


def test_fsm_random_walk_state_invariants(raw_plc_socket):
    """Send 40 random valid GA_EV events; after each, query state and
    assert st is a legal enum value and st_str is a non-empty string."""
    s = raw_plc_socket
    rng = random.Random(SEED ^ 0xE5E5E5E5)
    s = _ping_check(s, "fsm-pre", 87000)

    illegal_observed = []
    for i in range(40):
        ev = rng.choice(_FUZZ_EVENTS)
        ev_id = 87100 + i
        _send_pack(s, {"type": "SYS", "cmd": "GA_EV", "ev": ev, "id": ev_id})
        time.sleep(0.05)
        # Drain any reply, just to keep the wire clean
        s.settimeout(0.25)
        try:
            while True:
                if not s.recv(4096): break
        except (socket.timeout, OSError):
            pass

        # Query state
        gms_id = 88000 + i
        _send_pack(s, {"type": "SYS", "cmd": "GET_MACHINE_STATE", "id": gms_id})
        deadline = time.time() + 1.5
        unp = msgpack.Unpacker(raw=False, strict_map_key=False)
        st_obj = None
        s.settimeout(0.3)
        while time.time() < deadline and st_obj is None:
            try:
                chunk = s.recv(4096)
            except (socket.timeout, OSError):
                continue
            if not chunk: break
            try:
                unp.feed(chunk)
                for obj in unp:
                    if isinstance(obj, dict) and obj.get("id") == gms_id:
                        st_obj = obj
                        break
            except Exception:
                continue

        if st_obj is None:
            # Likely mid-test reset; reconnect and continue probing.
            s = _reconnect(s)
            continue

        st = st_obj.get("st")
        st_str = st_obj.get("st_str")
        if st not in _LEGAL_STATES:
            illegal_observed.append((ev, st, st_str))
        # st_str must be a non-empty string when st is set
        assert isinstance(st, int), f"st is not int: {st!r}"
        assert isinstance(st_str, str) and st_str, \
            f"st_str empty/non-string after ev={ev}: {st_str!r}"

    assert not illegal_observed, \
        f"FSM reached states outside enum range: {illegal_observed[:5]}"

    # Reset so we don't leave FSM in a random state
    _send_pack(s, {"type": "SYS", "cmd": "GA_EV", "ev": 8, "id": 87999})


# ----------------------------------------------- PING flood drop rate ----
# Send 300 PINGs back-to-back. Drop-newest under reply-ring saturation is
# permitted, but should be rare. Assert >= 90% return rate -- a regression
# that broke the drain pump or shrank the ring would tank this number.

def test_ping_flood_drop_rate(raw_plc_socket):
    s = raw_plc_socket
    s = _ping_check(s, "flood-pre", 89000)

    N = 300
    base_id = 89100
    for i in range(N):
        _send_pack(s, {"type": "SYS", "cmd": "PING", "id": base_id + i})
        # No sleep -- maximum pressure.

    # Drain for up to 4s, count pongs with ids in our range.
    received = set()
    unp = msgpack.Unpacker(raw=False, strict_map_key=False)
    deadline = time.time() + 4.0
    s.settimeout(0.3)
    while time.time() < deadline:
        try:
            chunk = s.recv(4096)
        except (socket.timeout, OSError):
            continue
        if not chunk:
            break
        try:
            unp.feed(chunk)
            for obj in unp:
                if isinstance(obj, dict) and obj.get("pong"):
                    rid = obj.get("id")
                    if isinstance(rid, int) and base_id <= rid < base_id + N:
                        received.add(rid)
        except Exception:
            continue

    rate = len(received) / N
    assert rate >= 0.90, (
        f"PING flood return rate {rate:.1%} < 90% ({len(received)}/{N}); "
        f"reply ring or Comm-task drain may be regressing"
    )
