"""Phase 3 verify (host-side): G1 `frame` field dispatch.

Runs as a regular CPython script (NOT via `rpc.py exec`) because we
need msgpack on the host side. Shells out to rpc.py for the two
daemon-side operations:
  - force GVL.Coord1Bound to a known value
  - read a GVL counter

Test plan:
  T1: force Coord1Bound=FALSE; send G1 frame=1 ->
      expect reply ack:FALSE err='coord1_not_bound' and
      GVL.Coord1NotBoundNakCount++
  T2: COORD1_BIND linear (-> Coord1Bound=TRUE);
      send G1 frame=1 -> counter MUST NOT increment
  T3: send G1 frame=0 (legacy) -> counter MUST NOT increment

Run: python codesys_scripts/jobs/templates/probe_coord1_g1_frame.py
"""
import os, sys, socket, time, subprocess, tempfile, msgpack

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
RPC = [sys.executable, os.path.join(REPO, "codesys_scripts", "rpc.py")]
PLC = ("192.168.1.70", 8125)

_id_seq = [70000]
def _id():
    _id_seq[0] += 1
    return _id_seq[0]


def daemon_exec(code):
    """Run inline IronPython code in the CODESYS daemon, return stdout."""
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(code)
        path = f.name
    try:
        out = subprocess.check_output(RPC + ["exec", "--file", path],
                                      stderr=subprocess.STDOUT)
        return out.decode("utf-8", "replace")
    finally:
        os.unlink(path)


def force_bound(val):
    daemon_exec("""
proj = projects.primary
app = next(iter(proj.find("Application", True)))
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)
oapp.set_prepared_value("GVL.Coord1Bound", "%s")
oapp.force_prepared_values()
""" % ("TRUE" if val else "FALSE"))


def unforce_all():
    daemon_exec("""
proj = projects.primary
app = next(iter(proj.find("Application", True)))
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)
oapp.unforce_all_values()
""")


def read_counter():
    out = daemon_exec("""
proj = projects.primary
app = next(iter(proj.find("Application", True)))
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)
print("CTR=%s" % oapp.read_value("GVL.Coord1NotBoundNakCount"))
""")
    for line in out.splitlines():
        if line.startswith("CTR="):
            raw = line[4:].strip()
            if "#" in raw:
                return int(raw.split("#", 1)[1].rstrip(")"))
            return int(raw)
    raise RuntimeError("counter line not found in: " + out)


def send_recv(sock, payload, timeout=2.0):
    pid = payload.setdefault("id", _id())
    sock.sendall(msgpack.packb(payload, use_bin_type=True))
    unp = msgpack.Unpacker(raw=False)
    deadline = time.time() + timeout
    sock.settimeout(0.4)
    while time.time() < deadline:
        try:
            d = sock.recv(4096)
        except socket.timeout:
            continue
        if not d:
            return None
        unp.feed(d)
        for obj in unp:
            if isinstance(obj, dict) and obj.get("id") == pid:
                return obj
    return None


EV_POWER_ON              = 2
EV_GROUP_ENABLE          = 4
EV_HOME_GO_FORCE_SKIP    = 7
EV_RESET                 = 8


def state(sock):
    r = send_recv(sock, {"type": "SYS", "cmd": "GET_MACHINE_STATE"})
    return r.get("st") if r else None


def ping(sock):
    send_recv(sock, {"type": "SYS", "cmd": "PING"}, timeout=0.3)


def drive_to_ready(sock, timeout=20.0):
    """Walk Error->UnInited->Powered->GroupEnabled->Ready via SYS GA_EV.
    Maintains 1Hz PING so the A3 supervisor doesn't trip."""
    def send_ev(ev):
        send_recv(sock, {"type": "SYS", "cmd": "GA_EV", "ev": ev})

    last_ping = 0
    last_st = None
    last_ev_ts = 0
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = state(sock)
        if st != last_st:
            print("    state ->", st)
            last_st = st
        if st == 70:
            print("    PLC Ready (st=70)")
            return True
        if time.time() - last_ping > 0.8:
            ping(sock)
            last_ping = time.time()
        # Don't spam events; one per state-change window
        if time.time() - last_ev_ts > 1.5:
            if st == 990:
                send_ev(EV_RESET); last_ev_ts = time.time()
            elif st == 10:  # UnInited
                send_ev(EV_POWER_ON); last_ev_ts = time.time()
            elif st == 30:  # Powered
                send_ev(EV_GROUP_ENABLE); last_ev_ts = time.time()
            elif st == 50:  # GroupEnabled
                send_ev(EV_HOME_GO_FORCE_SKIP); last_ev_ts = time.time()
            elif st == 60:  # Homing
                pass  # wait for completion
        time.sleep(0.3)
    print("    drive_to_ready: timeout, final st=", state(sock))
    return False


def main():
    sock = socket.socket()
    sock.connect(PLC)
    sock.settimeout(2.0)

    print("--- drive PLC to Ready ---")
    if not drive_to_ready(sock):
        print("PLC could not reach Ready -- aborting")
        sock.close()
        return 2
    # The Ready->coord-configured leap requires SetCoord0/1 (type:M).
    # Send SetCoord0 now so the G1 frame branch is reachable.
    r = send_recv(sock, {"type": "M", "cmd": "SetCoord0"})
    print("    SetCoord0 reply:", r)

    passed = 0
    failed = 0
    def need(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1
            print("  OK:", msg)
        else:
            failed += 1
            print("  FAIL:", msg)

    ping(sock)
    print("--- T1: G1 frame=1 with Coord1Bound=FALSE -> coord1_not_bound NAK ---")
    force_bound(False)
    time.sleep(0.1)
    n0 = read_counter()
    r = send_recv(sock, {"type": "M", "cmd": "G1",
                         "frame": 1,
                         "X": 100.0, "Y": 0.0, "Z": 0.0, "F": 100.0})
    time.sleep(0.2)
    n1 = read_counter()
    print("    reply =", r)
    print("    counter %d -> %d" % (n0, n1))
    need(r is not None and r.get("ack") is False
         and r.get("err") == "coord1_not_bound",
         "NAK err='coord1_not_bound'")
    need(n1 == n0 + 1, "Coord1NotBoundNakCount incremented by 1")

    ping(sock)
    print("--- T2: COORD1_BIND linear + G1 frame=1 -> no coord1_not_bound NAK ---")
    unforce_all()  # release the T1 force on Coord1Bound so the bind handler can flip it TRUE
    time.sleep(0.2)
    r = send_recv(sock, {"type": "SYS", "cmd": "COORD1_BIND",
                         "ref_pulse": 0,
                         "ref_xyz": [0.0, 0.0, 0.0],
                         "scale":   [10.0, 0.0, 0.0]})
    need(r and r.get("ack") and r.get("mode") == "linear",
         "linear bind ack: %r" % (r,))
    n0 = read_counter()
    r = send_recv(sock, {"type": "M", "cmd": "G1",
                         "frame": 1,
                         "X": 100.0, "Y": 0.0, "Z": 0.0, "F": 100.0})
    time.sleep(0.2)
    n1 = read_counter()
    print("    reply =", r)
    print("    counter %d -> %d" % (n0, n1))
    need(n1 == n0,
         "Coord1NotBoundNakCount UNCHANGED while bound")

    ping(sock)
    print("--- T3: regression G1 frame=0 -> no coord1_not_bound NAK ---")
    n0 = read_counter()
    r = send_recv(sock, {"type": "M", "cmd": "G1",
                         "frame": 0,
                         "X": 100.0, "Y": 0.0, "Z": 0.0, "F": 100.0})
    time.sleep(0.2)
    n1 = read_counter()
    print("    reply =", r)
    print("    counter %d -> %d" % (n0, n1))
    need(n1 == n0,
         "Coord1NotBoundNakCount UNCHANGED on frame=0 (legacy)")

    print("\n%d passed, %d failed" % (passed, failed))
    sock.close()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
