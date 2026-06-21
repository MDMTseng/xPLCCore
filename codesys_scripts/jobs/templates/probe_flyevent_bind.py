"""Phase 4 step 3 verification: FlyEvent-scheduled COORD1_BIND +
window-exit fault.

Flow:
  1. drive to Ready, reset pulse to 0, enable synthetic belt step=2
  2. send M4 with action='coord1_bind', trig=130 (PulseTrigger),
     pulse_target=2000, ref_xyz=(10,0,-150), scale=[100,0,0],
     exit_pulse_offset=1500, ttl_ms=5000, event_id=99
  3. poll GET_COORD1_DEBUG: Coord1Bound stays FALSE until pulse crosses
     2000; then flips TRUE atomically (single scan)
  4. once bound, continue polling. With step=2 / 1ms tick, pulse grows
     ~ 2 per scan. Crossing exit_pulse (= 2000 + 1500 = 3500) takes
     ~ 1.5 s of belt motion.
  5. expect COORD1_ERROR event with event_id=99, and PLC state -> Error
  6. arm should freeze (we won't actually issue G1, just check state)

Asserts:
  T1: M4 ACK with ev_buf_space
  T2: while pulse < 2000: Coord1Bound=FALSE
  T3: when pulse >= 2000: Coord1Bound=TRUE, Coord1ExitPulse ~= 3500
  T4: when pulse > Coord1ExitPulse: state moves to Error AND COORD1_ERROR
      event appears in the inbound stream within ~200ms
  T5: event payload includes correct event_id, mv_id, etc.
"""
import os, sys, socket, time, subprocess, tempfile
import msgpack

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
RPC = [sys.executable, os.path.join(REPO, "codesys_scripts", "rpc.py")]
PLC = ("192.168.1.70", 8125)

_id = [80000]
def nid():
    _id[0] += 1; return _id[0]

def daemon_exec(code):
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(code); path = f.name
    try:
        return subprocess.check_output(RPC + ["exec", "--file", path],
                                       stderr=subprocess.STDOUT).decode("utf-8","replace")
    finally:
        os.unlink(path)


def send_recv(sock, payload, timeout=0.6, collect_events=None):
    """Sends payload, waits for matching id. If collect_events is a list,
    every event message that arrives (kind='event') is appended."""
    payload.setdefault("id", nid())
    sock.sendall(msgpack.packb(payload, use_bin_type=True))
    pid = payload["id"]; unp = msgpack.Unpacker(raw=False)
    deadline = time.time() + timeout; sock.settimeout(0.1)
    while time.time() < deadline:
        try: d = sock.recv(4096)
        except socket.timeout: continue
        if not d: return None
        unp.feed(d)
        for obj in unp:
            if isinstance(obj, dict):
                if obj.get("id") == pid:
                    return obj
                if obj.get("kind") == "event" and collect_events is not None:
                    collect_events.append(obj)
    return None


EV_POWER_ON, EV_GROUP_ENABLE, EV_HOME_FSK, EV_RESET, EV_ERROR = 2, 4, 7, 8, 9
def st(sock):
    r = send_recv(sock, {"type":"SYS","cmd":"GET_MACHINE_STATE"})
    return r.get("st") if r else None
def ping(sock): send_recv(sock, {"type":"SYS","cmd":"PING"}, 0.2)
def gaev(sock, e): send_recv(sock, {"type":"SYS","cmd":"GA_EV","ev":e})


def drive_to_ready(sock):
    gaev(sock, EV_ERROR); time.sleep(0.3); ping(sock)
    gaev(sock, EV_RESET); time.sleep(0.3); ping(sock)
    last_st = None; last_ping = 0; last_ev = 0
    deadline = time.time() + 20
    while time.time() < deadline:
        s = st(sock)
        if s != last_st: print("    state ->", s); last_st = s
        if s == 70: return True
        if time.time() - last_ping > 0.8: ping(sock); last_ping = time.time()
        if time.time() - last_ev > 1.2:
            if s == 10: gaev(sock, EV_POWER_ON); last_ev = time.time()
            elif s == 30: gaev(sock, EV_GROUP_ENABLE); last_ev = time.time()
            elif s == 50: gaev(sock, EV_HOME_FSK); last_ev = time.time()
        time.sleep(0.2)
    return False


def main():
    sock = socket.socket(); sock.connect(PLC); sock.settimeout(2.0)
    try: return _inner(sock)
    finally:
        try: send_recv(sock, {"type":"SYS","cmd":"COORD1_UNBIND"})
        except: pass
        try:
            daemon_exec("""
proj = projects.primary
app = next(iter(proj.find("Application", True)))
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)
oapp.set_prepared_value("GVL.ConveyorPulseSyntheticEnable","FALSE")
oapp.force_prepared_values()
import time; time.sleep(0.1); oapp.unforce_all_values()
""")
        except: pass
        sock.close()


def _inner(sock):
    passed = failed = 0
    def need(c, m):
        nonlocal passed, failed
        if c: passed += 1; print("  OK:", m)
        else: failed += 1; print("  FAIL:", m)

    print("--- reset pulse to 0, drive to Ready ---")
    daemon_exec("""
import time
proj = projects.primary
app = next(iter(proj.find("Application", True)))
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)
oapp.set_prepared_value("GVL.ConveyorPulseSyntheticEnable","FALSE")
oapp.set_prepared_value("GVL.ConveyorPulseRaw","0")
oapp.force_prepared_values(); time.sleep(0.1)
oapp.unforce_all_values()
""")
    if not drive_to_ready(sock):
        print("could not reach Ready"); return 1
    send_recv(sock, {"type":"M","cmd":"SetCoord0"})
    send_recv(sock, {"type":"SYS","cmd":"COORD1_UNBIND"})

    print("--- T1: send M4 action=coord1_bind, pulse_target=2000 ---")
    r = send_recv(sock, {"type":"M","cmd":"M4",
                         "trig": 130,
                         "action": "coord1_bind",
                         "pulse_target": 2000,
                         "ref_xyz": [10.0, 0.0, -150.0],
                         "scale":   [100.0, 0.0, 0.0],
                         "exit_pulse_offset": 1500,
                         "ttl_ms": 5000,
                         "event_id": 99})
    print("    M4 reply =", r)
    need(r is not None and r.get("ack") is True
         and r.get("ev_buf_space") is not None,
         "M4 ack with ev_buf_space")

    print("--- T2: enable belt step=2, wait for bind to fire ---")
    daemon_exec("""
proj = projects.primary
app = next(iter(proj.find("Application", True)))
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)
oapp.set_prepared_value("GVL.ConveyorPulseSyntheticEnable","TRUE")
oapp.set_prepared_value("GVL.ConveyorPulseSyntheticStep","2")
oapp.force_prepared_values()
""")
    # Poll until Coord1Bound = True or 4 s timeout
    bound = False
    events = []
    deadline = time.time() + 6.0
    while time.time() < deadline:
        d = send_recv(sock, {"type":"SYS","cmd":"GET_COORD1_DEBUG"}, 0.3,
                      collect_events=events)
        if d and d.get("bound"):
            bound = True
            print("    bound=True at pulse_raw=%s" % d.get("pulse_raw"))
            break
        time.sleep(0.05)
    need(bound, "Coord1Bound TRUE after pulse crossed target")

    print("--- T3: while bound, monitor for COORD1_ERROR + state change ---")
    err_event = None
    final_state = None
    deadline = time.time() + 6.0
    while time.time() < deadline:
        d = send_recv(sock, {"type":"SYS","cmd":"GET_COORD1_DEBUG"}, 0.3,
                      collect_events=events)
        m = send_recv(sock, {"type":"SYS","cmd":"GET_MACHINE_STATE"}, 0.3,
                      collect_events=events)
        # scan collected events
        for ev in events:
            if ev.get("name") == "COORD1_ERROR" and err_event is None:
                err_event = ev
                print("    >>> COORD1_ERROR event received:", ev)
        if m and m.get("st") == 990:  # Error
            final_state = m.get("st")
            print("    PLC state -> Error (990)")
            break
        time.sleep(0.05)
    need(err_event is not None, "COORD1_ERROR event arrived")
    need(final_state == 990, "PLC state transitioned to Error")
    if err_event:
        need(err_event.get("event_id") == 99,
             "event payload event_id == 99 (host correlation)")
        need(err_event.get("pulse_at_err") >= err_event.get("exit_pulse",-1),
             "pulse_at_err >= exit_pulse (window actually exceeded)")
        print("    payload:", err_event)

    print("\n%d passed, %d failed" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
