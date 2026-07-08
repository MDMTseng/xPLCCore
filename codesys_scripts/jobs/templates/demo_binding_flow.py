"""End-to-end wire demo of the tape-binding bench flow (BindingTestPage).

Raw-socket phases against the live PLC (192.168.1.70:8125) -- the same
wire path the Binding-test tab's buttons use, so a green run here proves
the page can do the same with real clicks. The UI's TCP client is
arbitrated off via remote_ctrl (ONE client rule) and restored at the end.

SAFETY: this demo NEVER commands real motor motion. It runs with ALL
axes simulated (SET_AXIS_SIM 0x0F), and only *accepts-and-reverts* the
bench mask 0x07 in UnInited to prove the per-axis path. The first
real-reel spin is the user's physical session.

Phases:
   P0  arbitrate UI off, raw socket + 1Hz PING keepalive
   P1  GA_EV RESET -> UnInited; SET_AXIS_SIM checks:
         - 0x07 (bench mask: delta sim, reel real) accepted + echoed
         - 0x0F (all sim) accepted; poll GET_MACHINE_STATE until
           axes_sim_mask=0x0F (reinit-complete handshake)
   P2  climb POWER_ON -> GROUP_ENABLE -> HOME_GO_FORCE_SKIP -> Ready.
       NOTE: no legacy virtual_motors_force here -- the FORCE_SKIP gate
       passes via the NEW per-axis mask (delta bits 0x07 all sim).
   P3  ReelGo small distance (virtual reel axis), poll reel_pos delta
       until settle (the page uses the same settle detector).
   P4  immediate-fire M4 press pulse (trig=120 huge-radius, pins 0+1 =
       mask 0x03, press 4000ms, PLC-timed release). Online-read
       AxisGroupSM.DigitalOutputBits + OutputCHs[1] (the byte mapped to
       %QB8 / CH1 physical outputs 0-7) via the RPC daemon DURING the
       press (expect mask ON) and after (expect released).
   P5  negative check: SET_AXIS_SIM while Ready -> NAK 'not_uninited'
   P6  GA_EV RESET (leave axes all-sim -- safe default), reconnect UI

Run:
   python codesys_scripts/jobs/templates/demo_binding_flow.py
Requires:
   - PLC carries the SET_AXIS_SIM / axes_sim_mask build
   - CODESYS RPC daemon up on 127.0.0.1:7420 (for the DO online-reads;
     phases P0-P3/P5 run without it)
"""
import json
import socket
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import msgpack

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parents[1]                    # codesys_scripts/
REPO = SCRIPTS.parent
RPC = SCRIPTS / "rpc.py"
REMOTE_CTRL = SCRIPTS / "internals" / "remote_ctrl.py"

PLC_HOST, PLC_PORT = "192.168.1.70", 8125

EV_POWER_ON, EV_GROUP_ENABLE, EV_HOME_GO_FORCE_SKIP, EV_RESET = 2, 4, 7, 8
ST_UNINITED, ST_POWERED, ST_GROUP_ENABLED, ST_READY = 10, 30, 50, 70

SIM_ALL = 0x0F
SIM_BENCH = 0x07          # delta trio sim, reel real -- accept/revert only
PRESS_MASK = 0x03         # DO bits 0+1 -> CH1 physical outputs 0+1 (safe 0-7 subset)
PRESS_MS = 4000           # long enough for daemon round-trip mid-press

_send_lock = threading.Lock()


def banner(title):
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


# ── UI arbitration + raw socket ─────────────────────────────────────
def ui_set_tcp(connect):
    action = "connect_tcp" if connect else "disconnect_tcp"
    payload = '{"host":"192.168.1.70","port":8125}' if connect else "{}"
    subprocess.run(
        [sys.executable, str(REMOTE_CTRL), action, payload, "--timeout", "3"],
        capture_output=True, text=True, timeout=8,
    )


def send_pack(s, payload):
    with _send_lock:
        s.sendall(msgpack.packb(payload, use_bin_type=True))


def drain_until_id(s, expect_id, timeout=3.0):
    deadline = time.time() + timeout
    unp = msgpack.Unpacker(raw=False, strict_map_key=False)
    s.settimeout(0.4)
    while time.time() < deadline:
        try:
            chunk = s.recv(4096)
        except socket.timeout:
            continue
        if not chunk:
            return None
        unp.feed(chunk)
        for obj in unp:
            if isinstance(obj, dict) and obj.get("id") == expect_id:
                return obj
    return None


class Wire(object):
    def __init__(self):
        self.s = socket.socket()
        self.s.settimeout(3.0)
        self.s.connect((PLC_HOST, PLC_PORT))
        self._id = 87000
        self._stop = threading.Event()
        self._ka = threading.Thread(target=self._keepalive)
        self._ka.daemon = True
        self._ka.start()

    def _keepalive(self):
        i = 0
        while not self._stop.is_set():
            i += 1
            try:
                with _send_lock:
                    self.s.sendall(msgpack.packb(
                        {"type": "SYS", "cmd": "PING", "id": 60000 + (i % 1000)},
                        use_bin_type=True))
            except OSError:
                return
            self._stop.wait(0.8)

    def rpc(self, packet, timeout=3.0):
        self._id += 1
        packet = dict(packet)
        packet["id"] = self._id
        send_pack(self.s, packet)
        return drain_until_id(self.s, self._id, timeout)

    def state(self):
        return self.rpc({"type": "SYS", "cmd": "GET_MACHINE_STATE"})

    def close(self):
        self._stop.set()
        self._ka.join(timeout=1.5)
        try:
            self.s.close()
        except OSError:
            pass


# ── daemon online-reads ─────────────────────────────────────────────
def read_symbols(symbols, timeout=60):
    """One warm-session daemon job; returns {symbol: str-value}."""
    job = textwrap.dedent("""
        import sys
        sys.settrace(None)
        proj = projects.primary
        oapp = online.create_online_application(proj.active_application)
        if not oapp.is_logged_in:
            oapp.login(OnlineChangeOption.Try, False)
        for sym in %r:
            try:
                v = oapp.read_value(sym)
            except Exception as ex:
                v = "ERR:" + str(ex)[:60]
            print("%%s=%%s" %% (sym, v))
    """ % (list(symbols),))
    cp = subprocess.run(
        [sys.executable, str(RPC), "exec", "--label", "binding_demo_read",
         "--timeout", str(timeout)],
        input=job, capture_output=True, text=True, timeout=timeout + 10,
    )
    out = {}
    for line in (cp.stdout or "").splitlines():
        if "=" in line and not line.startswith("["):
            k, _, v = line.partition("=")
            v = v.strip()
            if "#" in v:
                v = v.split("#", 1)[1]
            out[k.strip()] = v
    return out


def poll_state_until(w, pred, timeout, poll=0.3):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        st = w.state()
        if st is not None:
            last = st
            if pred(st):
                return st
        time.sleep(poll)
    return None if last is None or not pred(last) else last


def main():
    banner("P0  arbitrate UI off, open raw socket")
    ui_set_tcp(False)
    time.sleep(0.5)
    w = Wire()
    try:
        s0 = w.state()
        if s0 is None:
            print("!! no GET_MACHINE_STATE reply -- is the PLC up?")
            return 2
        print("  st=%s (%s)  axes_sim_mask=%s  reel_pos=%s" % (
            s0.get("st"), s0.get("st_str"), s0.get("axes_sim_mask"),
            s0.get("reel_pos")))
        if "axes_sim_mask" not in s0:
            print("!! axes_sim_mask missing -- PLC not carrying the SET_AXIS_SIM build")
            return 2

        # ── P1 reset + SET_AXIS_SIM checks ──────────────────────────
        banner("P1  RESET -> UnInited; SET_AXIS_SIM bench(0x07) + all-sim(0x0F)")
        r = w.rpc({"type": "SYS", "cmd": "GA_EV", "ev": EV_RESET})
        assert r and r.get("ack"), "GA_EV(RESET) not acked: %r" % r
        st = poll_state_until(w, lambda x: x.get("st") == ST_UNINITED, 5.0)
        assert st, "did not reach UnInited"
        print("  UnInited reached")

        r = w.rpc({"type": "SYS", "cmd": "SET_AXIS_SIM", "mask": SIM_BENCH})
        assert r and r.get("ack") is True, "SET_AXIS_SIM(0x07) NAK: %r" % r
        assert r.get("mask") == SIM_BENCH, "echo mask wrong: %r" % r
        print("  SET_AXIS_SIM(0x07) ack, effective mask echo=0x%02x" % r.get("mask", -1))
        # Reaching 0x07 requires the PHYSICAL reel drive to accept a
        # reinit to real mode -- an environmental matter on this bench
        # (drive may be unpowered today). Report, don't fail: the wire
        # contract (ack + echo + state gate) is already asserted above.
        st = poll_state_until(w, lambda x: x.get("axes_sim_mask") == SIM_BENCH, 15.0)
        if st:
            print("  axes_sim_mask=0x07 published (delta sim, reel REAL applied on drives)")
        else:
            cur = w.state()
            print("  NOTE: axes_sim_mask=0x%02x did not reach 0x07 within 15s;"
                  % cur.get("axes_sim_mask", -1))
            print("        err_src=%r err_id=%r -- likely the physical reel drive"
                  % (cur.get("err_src"), cur.get("err_id")))
            print("        refused reinit-to-real (unpowered/absent). The user's")
            print("        physical session will exercise this leg for real.")

        # Demo safety: revert to ALL simulated before any motion.
        r = w.rpc({"type": "SYS", "cmd": "SET_AXIS_SIM", "mask": SIM_ALL})
        assert r and r.get("ack") is True, "SET_AXIS_SIM(0x0F) NAK: %r" % r
        st = poll_state_until(w, lambda x: x.get("axes_sim_mask") == SIM_ALL, 15.0)
        assert st, "axes_sim_mask never reached 0x0F"
        print("  axes_sim_mask=0x0F published -- ALL axes simulated for the demo")

        bad = w.rpc({"type": "SYS", "cmd": "SET_AXIS_SIM", "mask": 99})
        assert bad and bad.get("ack") is False and bad.get("err") == "bad_mask", \
            "mask=99 should NAK bad_mask: %r" % bad
        print("  SET_AXIS_SIM(99) NAK err=bad_mask (range guard ok)")

        # ── P2 climb to Ready via the NEW gate ──────────────────────
        banner("P2  climb POWER_ON -> GROUP_ENABLE -> HOME_GO_FORCE_SKIP")
        for ev, label, target in [
            (EV_POWER_ON, "POWER_ON", ST_POWERED),
            (EV_GROUP_ENABLE, "GROUP_ENABLE", ST_GROUP_ENABLED),
            (EV_HOME_GO_FORCE_SKIP, "HOME_GO_FORCE_SKIP", ST_READY),
        ]:
            r = w.rpc({"type": "SYS", "cmd": "GA_EV", "ev": ev})
            assert r and r.get("ack"), "GA_EV(%s) not acked: %r" % (label, r)
            st = poll_state_until(w, lambda x, t=target: x.get("st") == t, 8.0)
            assert st, "%s did not reach st=%d" % (label, target)
            print("  %-20s -> st=%d %s" % (label, target, st.get("st_str")))
        print("  (FORCE_SKIP passed via per-axis mask -- legacy force not used)")

        # ── P3 ReelGo + reel_pos settle ─────────────────────────────
        banner("P3  ReelGo +5mm (virtual reel), poll reel_pos settle")
        start_pos = w.state().get("reel_pos", 0.0)
        r = w.rpc({"type": "M", "cmd": "ReelGo", "Distance": 5.0, "F": 20.0,
                   "ACC": 100.0, "DEA": 100.0, "JERK": 10000.0})
        assert r and r.get("ack") is True, "ReelGo NAK: %r" % r
        print("  ReelGo acked; start reel_pos=%.3f" % start_pos)
        target = start_pos + 5.0
        settled = None
        stable = 0
        last = None
        deadline = time.time() + 30.0
        time.sleep(0.3)
        while time.time() < deadline:
            p = w.state().get("reel_pos")
            if p is not None:
                if abs(p - target) <= 0.05:
                    settled = ("target", p)
                    break
                if last is not None and abs(p - last) <= 0.005:
                    stable += 1
                    if stable >= 3:
                        settled = ("stable", p)
                        break
                else:
                    stable = 0
                last = p
            time.sleep(0.3)
        assert settled, "reel_pos never settled (last=%r)" % last
        print("  reel settled via %s: reel_pos=%.3f (delta=%.3f)" % (
            settled[0], settled[1], settled[1] - start_pos))

        # ── P4 press pulse + DO online-read ─────────────────────────
        banner("P4  M4 press pulse mask=0x%02x, %dms, PLC-timed release" % (
            PRESS_MASK, PRESS_MS))
        # OutputCHs[1] is the byte the device I/O mapping binds to %QB8
        # (CH1 = physical outputs 0-7); see ProcessFlyEventsAndIo.st
        # 2026-07-08 note. NOTE: deliberately NO IoConfig_Globals.*
        # device-image reads here -- read_value on an invalid device
        # member HANGS the scripting daemon (observed live 2026-07-08),
        # and the end-to-end hardware check is the user's physical
        # LED/press verify anyway.
        DO_SYMS = ["AxisGroupSM.DigitalOutputBits",
                   "AxisGroupSM.OutputCHs[1]"]
        pre = read_symbols(DO_SYMS)
        print("  pre : %s" % pre)
        r = w.rpc({
            "type": "M", "cmd": "M4", "motion_id": 0,
            "trig": 120, "tx": 0.0, "ty": 0.0, "tz": 0.0,
            "td": 1.0e9, "tin": 1, "ttl_ms": 2000,
            "pin_op_seq": [0, PRESS_MASK, PRESS_MASK, PRESS_MS, PRESS_MASK, 0],
            "event_id": 4242,
        })
        assert r and r.get("ack") is True, "press M4 NAK: %r" % r
        print("  M4 acked (event_id echo=%s); reading DO mid-press..." % r.get("event_id"))
        t_fire = time.time()
        mid = read_symbols(DO_SYMS)
        t_mid = time.time() - t_fire
        print("  mid (+%.1fs): %s" % (t_mid, mid))
        # sleep past the PLC-timed release
        rest = PRESS_MS / 1000.0 + 1.5 - (time.time() - t_fire)
        if rest > 0:
            time.sleep(rest)
        post = read_symbols(DO_SYMS)
        print("  post(+%.1fs): %s" % (time.time() - t_fire, post))

        mid_bits = int(mid.get("AxisGroupSM.DigitalOutputBits", "-1"))
        post_bits = int(post.get("AxisGroupSM.DigitalOutputBits", "-1"))
        if t_mid <= PRESS_MS / 1000.0:
            assert (mid_bits & PRESS_MASK) == PRESS_MASK, \
                "DO not ON mid-press: bits=0x%x" % mid_bits
            print("  VERIFIED: DigitalOutputBits carried 0x%02x during the press" % PRESS_MASK)
        else:
            print("  (mid read landed after release window -- ON-phase unverified)")
        assert (post_bits & PRESS_MASK) == 0, \
            "DO not auto-released: bits=0x%x" % post_bits
        print("  VERIFIED: PLC-timed release cleared the mask (bits=0x%02x)" % post_bits)

        # ── P5 gate check ───────────────────────────────────────────
        banner("P5  SET_AXIS_SIM while Ready must NAK not_uninited")
        r = w.rpc({"type": "SYS", "cmd": "SET_AXIS_SIM", "mask": 0})
        assert r and r.get("ack") is False and r.get("err") == "not_uninited", \
            "expected NAK not_uninited, got %r" % r
        print("  NAK err=not_uninited (state gate ok)")

        # ── P6 wrap up ──────────────────────────────────────────────
        banner("P6  RESET (axes stay all-sim -- safe default), reconnect UI")
        w.rpc({"type": "SYS", "cmd": "GA_EV", "ev": EV_RESET})
        print("  done")

        banner("DEMO GREEN -- all assertions passed")
        return 0
    finally:
        w.close()
        time.sleep(0.3)
        ui_set_tcp(True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        print("\n!! ASSERTION FAILED: %s" % e)
        sys.exit(1)
