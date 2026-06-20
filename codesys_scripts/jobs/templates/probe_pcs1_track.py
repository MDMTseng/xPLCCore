"""Phase 4 step 2 verification: MC_TrackConveyorBelt links PCS_1.

Wire-level checks:
  T1: COORD1_UNBIND (clear any stale bind) -> ack
  T2: COORD1_BIND linear  (default belt_kind, scale[0]=100, ref_pulse=1000,
      ref_xyz=[500,0,0]) -> ack, mode='linear'
  T3: GET_COORD1_DEBUG -> bound=True; diagnostic origin_x matches
      formula RefXyz + (Pulse-RefPulse)/Scale (this is OUR prediction;
      the kernel-side tcb may differ -- this just verifies the
      diagnostic path still works after the rewrite).
  T4: re-bind without unbind -> NAK 'rebind_requires_unbind'
  T5: COORD1_UNBIND -> ack -> COORD1_BIND with scale[1]!=0 ->
      NAK 'belt_direction_not_axis_x'
  T6: drive PLC to Ready, COORD1_BIND linear, send G1 frame=1 ->
      ack with movement_id (proves CoordSystem := PCS_1 path runs and
      MC accepts; we don't try to verify physical convergence on
      virtual axes due to the self-halt rule).
"""
import os, sys, socket, time, subprocess, tempfile, msgpack

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
RPC = [sys.executable, os.path.join(REPO, "codesys_scripts", "rpc.py")]
PLC = ("192.168.1.70", 8125)

_id_seq = [50000]
def _id():
    _id_seq[0] += 1
    return _id_seq[0]


def daemon_exec(code):
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(code)
        path = f.name
    try:
        return subprocess.check_output(RPC + ["exec", "--file", path],
                                       stderr=subprocess.STDOUT).decode("utf-8", "replace")
    finally:
        os.unlink(path)


def force_pulse(val):
    daemon_exec("""
proj = projects.primary
app = next(iter(proj.find("Application", True)))
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)
oapp.set_prepared_value("GVL.ConveyorPulseRaw", "%d")
oapp.force_prepared_values()
""" % int(val))


def unforce_all():
    daemon_exec("""
proj = projects.primary
app = next(iter(proj.find("Application", True)))
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)
oapp.unforce_all_values()
""")


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


EV_POWER_ON, EV_GROUP_ENABLE, EV_HOME_GO_FORCE_SKIP, EV_RESET, EV_ERROR = 2, 4, 7, 8, 9

def state(sock):
    r = send_recv(sock, {"type": "SYS", "cmd": "GET_MACHINE_STATE"})
    return r.get("st") if r else None

def ping(sock):
    send_recv(sock, {"type": "SYS", "cmd": "PING"}, timeout=0.3)

def drive_to_ready(sock, timeout=20.0):
    def send_ev(ev):
        send_recv(sock, {"type": "SYS", "cmd": "GA_EV", "ev": ev})
    last_ping = 0; last_ev_ts = 0; last_st = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = state(sock)
        if st != last_st:
            print("    state ->", st); last_st = st
        if st == 70:
            return True
        if time.time() - last_ping > 0.8:
            ping(sock); last_ping = time.time()
        if time.time() - last_ev_ts > 1.5:
            if st == 990: send_ev(EV_RESET); last_ev_ts = time.time()
            elif st == 10: send_ev(EV_POWER_ON); last_ev_ts = time.time()
            elif st == 30: send_ev(EV_GROUP_ENABLE); last_ev_ts = time.time()
            elif st == 50: send_ev(EV_HOME_GO_FORCE_SKIP); last_ev_ts = time.time()
        time.sleep(0.3)
    return False


def main():
    sock = socket.socket(); sock.connect(PLC); sock.settimeout(2.0)
    try:
        return _inner(sock)
    finally:
        try: unforce_all()
        except Exception as e: print("unforce_all exit FAIL:", e)
        sock.close()


def _inner(sock):
    passed = failed = 0
    def need(c, m):
        nonlocal passed, failed
        if c: passed += 1; print("  OK:", m)
        else: failed += 1; print("  FAIL:", m)

    print("--- T1: COORD1_UNBIND clears any prior bind ---")
    r = send_recv(sock, {"type": "SYS", "cmd": "COORD1_UNBIND"})
    print("    reply =", r)
    need(r is not None and r.get("ack") is True, "UNBIND ack")
    d = send_recv(sock, {"type": "SYS", "cmd": "GET_COORD1_DEBUG"})
    print("    bound after unbind =", d.get("bound"))
    need(d.get("bound") is False, "bound=False after UNBIND")

    print("--- T2: COORD1_BIND linear ---")
    force_pulse(1000)
    time.sleep(0.1)
    r = send_recv(sock, {"type": "SYS", "cmd": "COORD1_BIND",
                         "ref_pulse": 1000,
                         "ref_xyz": [500.0, 0.0, 0.0],
                         "scale":   [100.0, 0.0, 0.0]})
    print("    reply =", r)
    need(r and r.get("ack") is True and r.get("mode") == "linear",
         "linear bind ack mode=linear")

    print("--- T3: GET_COORD1_DEBUG -- diagnostic origin_x sanity ---")
    d = send_recv(sock, {"type": "SYS", "cmd": "GET_COORD1_DEBUG"})
    print("    debug =", d)
    need(d.get("bound") is True, "bound=True after BIND")
    # OriginNow at pulse=1000 == RefXyz[0] = 500 (formula gives delta=0)
    need(abs(d.get("origin_x", 0) - 500.0) < 0.1, "origin_x = 500 at pulse=1000")
    # Advance pulse to 2000; expect origin_x = 500 + (2000-1000)/100 = 510
    force_pulse(2000)
    time.sleep(0.15)
    d = send_recv(sock, {"type": "SYS", "cmd": "GET_COORD1_DEBUG"})
    need(abs(d.get("origin_x", 0) - 510.0) < 0.1,
         "origin_x = 510 at pulse=2000 (delta 10mm)")

    print("--- T4: re-bind without unbind -> NAK 'rebind_requires_unbind' ---")
    r = send_recv(sock, {"type": "SYS", "cmd": "COORD1_BIND",
                         "ref_pulse": 5000,
                         "ref_xyz": [0.0, 0.0, 0.0],
                         "scale":   [100.0, 0.0, 0.0]})
    print("    reply =", r)
    need(r and r.get("ack") is False
         and r.get("err") == "rebind_requires_unbind", "rebind NAK")

    print("--- T5: belt direction not axis X -> NAK ---")
    send_recv(sock, {"type": "SYS", "cmd": "COORD1_UNBIND"})
    time.sleep(0.05)
    r = send_recv(sock, {"type": "SYS", "cmd": "COORD1_BIND",
                         "ref_pulse": 0, "ref_xyz": [0,0,0],
                         "scale":     [0.0, 100.0, 0.0]})  # Y-axis belt
    print("    reply =", r)
    need(r and r.get("ack") is False
         and r.get("err") == "belt_direction_not_axis_x",
         "NAK belt_direction_not_axis_x")

    print("--- T6: drive to Ready, G1 frame=1 -> ack with movement_id ---")
    # virtual motors should already be on if a prior session enabled them;
    # otherwise the drive will stall at GroupEnabled. Run helper if needed.
    if not drive_to_ready(sock, timeout=5.0):
        print("    enabling virtual motors via daemon...")
        daemon_exec("""
import time
proj = projects.primary
app = next(iter(proj.find("Application", True)))
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)
oapp.set_prepared_value("GVL.bVirtualMotorsMode_Request","FALSE")
oapp.force_prepared_values(); time.sleep(0.4)
oapp.unforce_all_values(); time.sleep(0.2)
oapp.set_prepared_value("GVL.bVirtualMotorsMode_Request","TRUE")
oapp.force_prepared_values()
""")
        # vmotors reset Coord1Bound too; re-bind for T6.
        send_recv(sock, {"type": "SYS", "cmd": "GA_EV", "ev": EV_ERROR})
        time.sleep(0.4); ping(sock)
        send_recv(sock, {"type": "SYS", "cmd": "GA_EV", "ev": EV_RESET})
        time.sleep(0.4); ping(sock)
        need(drive_to_ready(sock, 20.0), "PLC reached Ready")

    send_recv(sock, {"type": "M", "cmd": "SetCoord0"})
    ping(sock)
    send_recv(sock, {"type": "SYS", "cmd": "COORD1_UNBIND"})
    time.sleep(0.1)
    r = send_recv(sock, {"type": "SYS", "cmd": "COORD1_BIND",
                         "ref_pulse": 2000,
                         "ref_xyz": [0.0, 0.0, 0.0],
                         "scale":   [100.0, 0.0, 0.0]})
    need(r and r.get("ack") is True, "bind for T6 OK")
    time.sleep(0.2)  # let tcb pick up edge
    r = send_recv(sock, {"type": "M", "cmd": "G1",
                         "frame": 1, "X": 0.0, "Y": 0.0, "Z": 50.0,
                         "F": 100.0})
    print("    G1 frame=1 reply =", r)
    need(r is not None and r.get("ack") is True
         and r.get("movement_id") is not None,
         "G1 frame=1 accepted with movement_id")

    print("\n%d passed, %d failed" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
