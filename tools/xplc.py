"""One client for the PLC's msgpack API, with two interchangeable transports.

    import xplc
    m = xplc.Machine()                      # through the UI's link (default)
    m = xplc.Machine(xplc.Direct())         # own TCP socket (close the UI's link first)
    m.ping(); m.fsm(); m.ec_stats()["lost"]
    m.sys("DWELL", **{"from": 0})           # any SYS command
    m.m("G1", X=0, Y=0, Z=-5, F=50, ACC=500, DEA=500, JERK=5000)

Errors are typed (PlcError subclasses) instead of text in a RuntimeError or
a SystemExit: catch Nak / Busy / BlockTimeout / NotReady / LinkDown /
ReplyTimeout. The PLC sends no numeric error codes yet (2026-10-04 review),
so the classes are derived from the `err` text in one place, `classify`.

machine.py keeps its module-level functions for the existing tools; its
link functions delegate here. New tools should use Machine directly, and
the plc_direct tools move to Machine(Direct()) one at a time.
"""

import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(_HERE)

import topology as tp  # noqa: E402

# GA_EV events the host may post (DrainHostPackets).
EV_POWER_ON, EV_GROUP_ENABLE, EV_HOME_GO, EV_HOME_SKIP, EV_RESET = 2, 4, 6, 7, 8
SYNC_OFFSET_MAX = 50        # >= 60 wedged the PLC's EtherCAT layer


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---- errors ---------------------------------------------------------------

class PlcError(RuntimeError):
    """Base of everything this module raises on the PLC's account."""


class Nak(PlcError):
    """The PLC answered ack=false. `cmd` and `err` (its text) are kept."""

    def __init__(self, cmd, err, reply=None):
        RuntimeError.__init__(self, "%s: %s" % (cmd, err))
        self.cmd, self.err, self.reply = cmd, err, reply


class Busy(Nak):
    """A one-at-a-time channel is in use (sim_apply_busy, sdo busy, ...)."""


class BlockTimeout(Nak):
    """A BLOCK_FOR_* / WAIT_FOR_* command gave up (block_timeout)."""


class NotReady(Nak):
    """A motion command while the FSM is not Ready (group_not_ready)."""


class LinkDown(PlcError):
    """No connection to the PLC (UI link not connected, socket closed)."""


class ReplyTimeout(PlcError):
    """The PLC did not answer in time (also a dropped-whole reply)."""


def classify(cmd, err, reply=None):
    """The Nak subclass for an `err` text (one place for the PLC's strings)."""
    e = str(err)
    if "busy" in e:
        return Busy(cmd, e, reply)
    if "block_timeout" in e or "timeout" in e and "wait" in e.lower():
        return BlockTimeout(cmd, e, reply)
    if "group_not_ready" in e:
        return NotReady(cmd, e, reply)
    return Nak(cmd, e, reply)


def _from_text(cmd, text):
    """Map an error text from a transport to a PlcError."""
    t = str(text)
    if "not connected" in t or "connection closed" in t.lower() or "refused" in t.lower():
        return LinkDown(t)
    if "reply timeout" in t or "no reply" in t:
        return ReplyTimeout(t)
    return classify(cmd, t)


# ---- transports -------------------------------------------------------------

class UiRelay:
    """Packets through the UI's own PLC link (remote harness on :8127 ->
    the UI's MotorTestPage `plc_send`). Shares the single client slot the
    PLC offers with the UI, so nothing else may hold a direct socket."""

    def __init__(self):
        sys.path.insert(0, os.path.join(_HERE, "sim"))
        argv, sys.argv = sys.argv, sys.argv[:1]      # run_virtual parses nothing at import
        import run_virtual
        sys.argv = argv
        self._push = run_virtual.push

    def push(self, action, payload=None, timeout=30.0):
        return self._push(action, payload or {}, timeout=timeout)

    def send(self, pkt, timeout_ms):
        try:
            return self.push("plc_send", {"pkt": pkt, "timeoutMs": timeout_ms}, timeout=timeout_ms / 1000 + 5)
        except RuntimeError as e:
            # The harness says "plc_send: <text>" and the UI "<cmd>: <err>";
            # keep the PLC's own err text.
            text = str(e)
            for prefix in ("plc_send: ", "%s: " % pkt.get("cmd")):
                if text.startswith(prefix):
                    text = text[len(prefix):]
            raise _from_text(pkt.get("cmd"), text)

    def reconnect(self, tries=20):
        """The UI's PLC link does not always come back after a download."""
        for _ in range(tries):
            try:
                self.push("plc_send", {"pkt": {"type": "SYS", "cmd": "PING"}, "timeoutMs": 3000}, timeout=8)
                return
            except Exception:
                try:
                    self.push("disconnect_tcp", timeout=20)
                    time.sleep(2)
                    self.push("connect_tcp", timeout=20)
                    time.sleep(2)
                except Exception:
                    time.sleep(3)
        raise LinkDown("the UI's link to the PLC did not come back")

    def close(self):
        pass


class Direct:
    """Own TCP socket to the PLC (plc_direct.Plc, with its 1 s PING). The
    PLC takes one client: the UI's link must be closed first."""

    def __init__(self, host=None, port=None, timeout=10.0, ping=True):
        import plc_direct
        self._plc_direct = plc_direct
        self.plc = plc_direct.Plc(host or tp.PLC_HOST, port or tp.PLC_PORT, timeout=timeout, ping=ping)

    def send(self, pkt, timeout_ms):
        try:
            return self.plc.send(pkt, timeout=timeout_ms / 1000.0)
        except self._plc_direct.Nak as e:
            text = str(e)
            err = text.split(": ", 1)[1] if ": " in text else text
            raise classify(pkt.get("cmd"), err)
        except TimeoutError as e:
            raise ReplyTimeout(str(e))
        except (ConnectionError, OSError) as e:
            raise LinkDown(str(e))

    def take_events(self, name=None):
        return self.plc.take_events(name)

    def close(self):
        self.plc.close()


# ---- the machine --------------------------------------------------------------

class Machine:
    def __init__(self, transport=None):
        self.t = transport or UiRelay()

    def close(self):
        self.t.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- raw ------------------------------------------------------------------

    def send(self, pkt, timeout_ms=5000):
        """One packet; the reply dict. A LinkDown is repaired once (UiRelay)
        and the packet sent again."""
        try:
            return self.t.send(pkt, timeout_ms)
        except LinkDown:
            if not hasattr(self.t, "reconnect"):
                raise
            self.t.reconnect()
            return self.t.send(pkt, timeout_ms)

    def sys(self, cmd, timeout_ms=5000, **kw):
        return self.send(dict(kw, type="SYS", cmd=cmd), timeout_ms)

    def m(self, cmd, timeout_ms=5000, **kw):
        return self.send(dict(kw, type="M", cmd=cmd), timeout_ms)

    # -- typed commands -------------------------------------------------------------

    def ping(self):
        return self.sys("PING")

    def machine_state(self):
        return self.sys("GET_MACHINE_STATE")

    def fsm(self):
        """(state name, reply) of the axis-group FSM."""
        r = self.sys("GA_EV", ev=0)
        return r["st_str"], r

    def event(self, ev):
        return self.sys("GA_EV", ev=ev)

    def fsm_to(self, want, home=True, timeout=120):
        """Walk the FSM to UnInited / Powered / Ready. Ready homes the real
        delta (EV_HOME_GO) or, when home=False, skips homing -- only valid
        while the delta is virtual (the PLC refuses it for real axes)."""
        end = time.time() + timeout
        while time.time() < end:
            st, r = self.fsm()
            if st == want:
                return r
            if st == "Error" and want != "UnInited":
                ev = EV_RESET
            elif want == "UnInited":
                ev = EV_RESET
            elif want == "Powered":
                ev = {"UnInited": EV_POWER_ON}.get(st, None if st == "Powering" else EV_RESET)
            else:
                ev = {"UnInited": EV_POWER_ON, "Powered": EV_GROUP_ENABLE,
                      "GroupEnabled": EV_HOME_GO if home else EV_HOME_SKIP}.get(st)
            if ev is not None:
                try:
                    self.event(ev)
                except PlcError:
                    pass
            time.sleep(0.5)
        st, r = self.fsm()
        raise PlcError("FSM did not reach %s (%s, %s %s)" % (want, st, r.get("err_src"), r.get("err_id")))

    def delta_mask(self):
        return self.machine_state().get("axes_sim_mask", -1) & 7

    def is_delta_virtual(self):
        return self.delta_mask() == 7

    def axis_info(self, axis):
        return self.sys("AXIS_INFO", axis=axis)

    def axis_states(self):
        return [self.axis_info(k)["st"] for k in range(3)]

    def drives_off(self):
        """FSM to UnInited and check every delta axis is power_off (st 0)."""
        self.fsm_to("UnInited")
        st = self.axis_states()
        if st != [0, 0, 0]:
            raise PlcError("delta not powered off: axis states %s" % st)
        return st

    def set_delta(self, real):
        """Real or virtual delta for this run of the PLC (a download brings
        back the project's virtual delta). Powers the delta off first."""
        self.drives_off()
        want = 0 if real else 7
        pkt = {"real": 1, "site_clear": 1} if real else {"real": 0}
        for i in range(30):
            try:
                self.sys("DELTA_MODE", **pkt)
                break
            except Busy:
                if i == 29:
                    raise
                time.sleep(1)
        for _ in range(60):
            if self.delta_mask() == want:
                return
            time.sleep(0.5)
        raise PlcError("delta mode did not change (mask %s)" % self.delta_mask())

    def ec_stats(self, **kw):
        return self.sys("EC_STATS", **kw)

    def task_stats(self):
        return self.sys("TASK_STATS")

    def dem_stats(self, **kw):
        return self.sys("DEM_STATS", **kw)

    def dwell(self, frm=0):
        return self.sys("DWELL", **{"from": frm})

    def direct(self, **kw):
        return self.sys("DIRECT", **kw)

    _drive_checked = None

    def drive_sdo(self, station, index, sub=0, size=4, value=None):
        """One SDO read (value) or write (value given) on a slave through
        the PLC. The first write to a station checks 0x1018:01 against the
        Delta vendor id (a slave reorder moved the addresses once)."""
        if self._drive_checked is None:
            self._drive_checked = set()
        if value is not None and station not in self._drive_checked:
            vendor = self.drive_sdo(station, 0x1018, sub=1)
            if vendor != tp.DELTA_VENDOR:
                raise PlcError("station %d is not a Delta drive (vendor 0x%X): no write" % (station, vendor))
            self._drive_checked.add(station)
        pkt = {"station": station, "index": index, "sub": sub, "size": size}
        if value is not None:
            pkt.update(value=int(value), write=1)
        for _ in range(50):
            try:
                seq = self.sys("DRV_SDO", **pkt)["seq_req"]
                break
            except Busy:
                time.sleep(0.2)
        else:
            raise Busy("DRV_SDO", "SDO channel stays busy")
        for _ in range(50):
            r = self.sys("DRV_SDO_RESULT")
            if not r["active"] and r["seq_res"] == seq:
                if not r["ok"]:
                    raise PlcError("SDO %s 0x%04X:%d on %d failed: error %s" % (
                        "write" if value is not None else "read", index, sub, station, r["sdo_err"]))
                return r["value"]
            time.sleep(0.2)
        raise ReplyTimeout("SDO 0x%04X:%d on %d: no result" % (index, sub, station))


# ---- safety gate ----------------------------------------------------------------

def require_owner_ok(flag=False):
    """The real delta moves only with the owner's OK for this run."""
    if flag or os.environ.get("XPLC_OWNER_OK") == "1":
        return
    raise SystemExit("REFUSED: this moves the real delta. Get the owner's OK for this run, "
                     "then pass --owner-ok (or set XPLC_OWNER_OK=1).")
