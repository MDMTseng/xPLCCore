"""Watch the EtherCAT bus at standstill for dropouts (2026-10-03: frames lost
for ~2.5 s now and then, even with the delta virtual and off; the reel and
QEC came back in INIT / SAFE-OP).

    python tools/bus_watch.py --owner-ok [--hours 6] [--every 10]
                              [--slaves-every 60] [--recover 0]

Every --every seconds it reads SYS EC_STATS (lost frames, rx errors); every
--slaves-every seconds it also asks the CODESYS daemon for the master's
xError / xConfigFinished and every slave's state. A dropout is any of: lost
frames or rx errors growing, the master in error or not configured, a
slave not in OP. The 10-05 09:10 dropout had Frames Lost 0 (the QEC left the
bus; watchdog 1002), so lost frames alone miss some.

On a dropout it first saves the evidence (machine.save_incident: PLC log,
EC_STATS, machine state, slave states) and then STOPS. With --recover N
(owner's OK only) it re-downloads instead (machine.safe_install: drives off,
download, slow-download refusal, EtherCAT check) up to N times. 2026-10-05
review: the old default re-downloaded 20 times with no gate and without
saving the PLC log first. Nothing moves.
"""

import argparse
import time

import machine as mc
import topology as tp
from machine import log

SLAVES = tp.SLAVE_NAMES
SHORT = {"ETC_SLAVE_STATE.ETC_SLAVE_OPERATIONAL": "OP", "ETC_SLAVE_STATE.ETC_SLAVE_SAVEOPERATIONAL": "SAFE-OP",
         "ETC_SLAVE_STATE.ETC_SLAVE_PREOPERATIONAL": "PRE-OP", "ETC_SLAVE_STATE.ETC_SLAVE_INIT": "INIT",
         "ETC_SLAVE_STATE.ETC_SLAVE_BOOT": "BOOT", "INT#0": "0"}
MASTER = "IoConfig_Globals.EtherCAT_Master_SoftMotion."


def short_name(s):
    return (s.replace("ASDA_B3_E_CoE_Drive", "EAxis").replace("_1", "1").replace("_2", "2")
            .replace("QEC_R11MP3S_V", "QEC").replace("reel_pull_motor", "reel"))


def bus_snapshot():
    """(ok, text): master flags and slave states through the daemon."""
    def read(sym):
        lines = mc.rpc("read", sym, timeout=60)
        val = lines[-1].strip() if lines else "?"
        if "no PLC session" in val or "not reachable" in val:
            # plc_guard refused the login (project differs) or no daemon:
            # unknown, not a dropout.
            raise RuntimeError(val)
        return val

    mc.rpc("logout", timeout=60)
    try:
        err, done = read(MASTER + "xError"), read(MASTER + "xConfigFinished")
        ok = "TRUE" not in err and "TRUE" in done
        parts = ["xError=%s" % err, "xConfigFinished=%s" % done]
        for s in SLAVES:
            v = read("IoConfig_Globals.%s.wState" % s)
            v = SHORT.get(v, v)
            parts.append("%s=%s" % (short_name(s), v))
            ok = ok and v == "OP"
    finally:
        mc.rpc("logout", timeout=60)
    return ok, " ".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6)
    ap.add_argument("--every", type=float, default=10)
    ap.add_argument("--slaves-every", type=float, default=60)
    ap.add_argument("--recover", type=int, default=0,
                    help="re-download after a dropout, up to N times (needs the owner's OK); default: stop")
    ap.add_argument("--owner-ok", action="store_true")
    a = ap.parse_args()
    mc.require_owner_ok(a.owner_ok)
    mc.reconnect()
    if mc.delta_mask() != 7 or mc.axis_states() != [0, 0, 0]:
        raise SystemExit("the delta must be virtual and off")
    t0 = time.time()
    drops = 0
    e = mc.sys_cmd("EC_STATS")
    last = (e["lost"], e["rx_err"])
    ok, snap = bus_snapshot()
    log("watch started, lost frames %d, rx errors %d, bus: %s" % (last[0], last[1], snap))
    if not ok:
        raise SystemExit("the bus is not healthy at the start: %s" % snap)
    next_snap = time.time() + a.slaves_every
    next_note = time.time() + 600
    while time.time() - t0 < a.hours * 3600:
        time.sleep(a.every)
        why = None
        try:
            e = mc.sys_cmd("EC_STATS")
            now = (e["lost"], e["rx_err"])
            if now[0] > last[0] or now[1] > last[1]:
                why = "lost frames %d -> %d, rx errors %d -> %d" % (last[0], now[0], last[1], now[1])
            last = now
        except Exception as ex:
            log("EC_STATS read failed: %s" % ex)
        if why is None and time.time() >= next_snap:
            next_snap = time.time() + a.slaves_every
            try:
                ok, snap = bus_snapshot()
                if not ok:
                    why = "bus: " + snap
            except Exception as ex:
                log("bus snapshot failed: %s" % ex)
        if why is None:
            if time.time() >= next_note:
                next_note += 600
                log("%.0f min: no dropout (lost frames %d, dropouts %d)" % ((time.time() - t0) / 60, last[0], drops))
            continue
        drops += 1
        log("DROPOUT %d at %.1f min: %s" % (drops, (time.time() - t0) / 60, why))
        try:
            ok, snap = bus_snapshot()
        except Exception as ex:
            snap = "unreadable: %s" % ex
        log("   bus: %s" % snap)
        log("   evidence: %s" % mc.save_incident("bus_watch", {"why": why, "bus": snap}))
        if drops > a.recover:
            log("=== stopped after dropout %d (re-download only with --recover and the owner's OK)" % drops)
            return
        log("   re-download %d of %d" % (drops, a.recover))
        try:
            mc.safe_install(read_log=False)       # the log is in the evidence folder already
        except SystemExit as ex:
            log("=== stopped: re-download failed: %s" % ex)
            return
        e = mc.sys_cmd("EC_STATS")
        last = (e["lost"], e["rx_err"])
    log("=== watch done: %.1f h, dropouts %d" % ((time.time() - t0) / 3600, drops))


if __name__ == "__main__":
    main()
