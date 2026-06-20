"""Phase 5 verification: M4 PulseTrigger (trig=130).

End-to-end exercise:
  T1: Register an M4 with trig=130, pulse_target=2000, one pin_op stage.
      Force GVL.ConveyorPulseRaw=1000 (< target) -> trigger should NOT
      fire. Counter IoTriggerCount unchanged.
  T2: Force GVL.ConveyorPulseRaw=2500 (>= target) -> trigger fires,
      IoTriggerCount += 1.
  T3: Register an M4 with trig=130, pulse_target=99999999 (unreachable),
      short ttl_ms=200. Pulse stays at 2500. After ~500ms the TTL must
      expire and emit a trigger error event (we don't intercept the
      event packet; we verify GVL.IoCommandCount accepted the M4 and
      that the FlyEvent slot is released, i.e. FlyEventAvailableCount
      returns to baseline).
"""
import os, sys, socket, time, subprocess, tempfile, msgpack

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
RPC = [sys.executable, os.path.join(REPO, "codesys_scripts", "rpc.py")]
PLC = ("192.168.1.70", 8125)

_id_seq = [60000]
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


def read_gvl(name):
    out = daemon_exec("""
proj = projects.primary
app = next(iter(proj.find("Application", True)))
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)
print("V=%%s" %% oapp.read_value("%s"))
""" % name)
    for line in out.splitlines():
        if line.startswith("V="):
            raw = line[2:].strip()
            if "#" in raw:
                return int(raw.split("#", 1)[1].rstrip(")"))
            try:
                return int(raw)
            except ValueError:
                return raw
    raise RuntimeError("V= line not found in: " + out)


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
    last_ping = 0
    last_ev_ts = 0
    last_st = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = state(sock)
        if st != last_st:
            print("    state ->", st)
            last_st = st
        if st == 70:
            return True
        if time.time() - last_ping > 0.8:
            ping(sock); last_ping = time.time()
        if time.time() - last_ev_ts > 1.5:
            if st == 990:
                send_ev(EV_RESET); last_ev_ts = time.time()
            elif st == 10:
                send_ev(EV_POWER_ON); last_ev_ts = time.time()
            elif st == 30:
                send_ev(EV_GROUP_ENABLE); last_ev_ts = time.time()
            elif st == 50:
                send_ev(EV_HOME_GO_FORCE_SKIP); last_ev_ts = time.time()
        time.sleep(0.3)
    return False


def main():
    sock = socket.socket()
    sock.connect(PLC)
    sock.settimeout(2.0)
    try:
        return _inner(sock)
    finally:
        try:
            unforce_all()
        except Exception as e:
            print("unforce_all on exit FAILED:", e)
        sock.close()


def diag(sock):
    return send_recv(sock, {"type": "SYS", "cmd": "GET_DIAG"})


def _inner(sock):
    passed = 0
    failed = 0
    def need(c, m):
        nonlocal passed, failed
        if c: passed += 1; print("  OK:", m)
        else: failed += 1; print("  FAIL:", m)

    print("--- clear any prior group_error_stop ---")
    send_recv(sock, {"type": "SYS", "cmd": "GA_EV", "ev": EV_ERROR})
    time.sleep(0.4); ping(sock)
    send_recv(sock, {"type": "SYS", "cmd": "GA_EV", "ev": EV_RESET})
    time.sleep(0.4); ping(sock)

    print("--- drive PLC to Ready ---")
    if not drive_to_ready(sock):
        print("could not reach Ready"); return 2
    send_recv(sock, {"type": "M", "cmd": "SetCoord0"})
    ping(sock)

    # Snapshot baseline counters.
    print("--- T1: register PulseTrigger, pulse below target -> no fire ---")
    force_pulse(1000)
    time.sleep(0.1)
    d0 = diag(sock)
    trig_count_before = d0.get("io_trig_count")
    avail_before = d0.get("flyevent_avail")
    print("    baseline io_trig_count=%s flyevent_avail=%s" % (trig_count_before, avail_before))

    r = send_recv(sock, {"type": "M", "cmd": "M4",
                         "trig": 130,
                         "pulse_target": 2000,
                         "ttl_ms": 10000,
                         "event_id": 42,
                         "pin_op_seq": [0, 0x800, 0x800]})
    print("    M4 reply =", r)
    need(r is not None and r.get("ack") is True,
         "M4 PulseTrigger registered (ack TRUE)")

    time.sleep(0.2)
    d1 = diag(sock)
    print("    after register: io_trig_count=%s flyevent_avail=%s" %
          (d1.get("io_trig_count"), d1.get("flyevent_avail")))
    need(d1.get("flyevent_avail") == avail_before - 1,
         "flyevent_avail dropped by 1 (slot occupied)")
    need(d1.get("io_trig_count") == trig_count_before,
         "io_trig_count UNCHANGED (pulse below target, no fire)")

    print("--- T2: force pulse over target -> trigger fires ---")
    force_pulse(2500)
    time.sleep(0.4)
    d2 = diag(sock)
    print("    after fire: io_trig_count=%s flyevent_avail=%s" %
          (d2.get("io_trig_count"), d2.get("flyevent_avail")))
    need(d2.get("io_trig_count") == trig_count_before + 1,
         "io_trig_count +=1 (PulseTrigger fired the pin_op stage)")
    need(d2.get("flyevent_avail") == avail_before,
         "flyevent_avail returned to baseline (slot released)")

    print("--- T3: TTL expiry on unreachable target ---")
    r = send_recv(sock, {"type": "M", "cmd": "M4",
                         "trig": 130,
                         "pulse_target": 99999999,
                         "ttl_ms": 200,
                         "event_id": 43,
                         "pin_op_seq": [0, 0x800, 0]})
    need(r is not None and r.get("ack") is True,
         "M4 TTL-event registered")
    time.sleep(0.1)
    d3 = diag(sock)
    need(d3.get("flyevent_avail") == avail_before - 1,
         "flyevent_avail dropped (TTL-event occupies slot)")
    # Pulse stays at 2500 (< 99999999). After 200ms TTL fires error and
    # the slot must be released. io_trig_count does NOT advance because
    # TTL expiry sends an error event, not a pin_op.
    time.sleep(0.5)
    d4 = diag(sock)
    print("    after TTL: io_trig_count=%s flyevent_avail=%s" %
          (d4.get("io_trig_count"), d4.get("flyevent_avail")))
    need(d4.get("flyevent_avail") == avail_before,
         "flyevent_avail recovered (TTL released the slot)")
    need(d4.get("io_trig_count") == trig_count_before + 1,
         "io_trig_count UNCHANGED by TTL expiry (still +1 from T2)")

    print("\n%d passed, %d failed" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
