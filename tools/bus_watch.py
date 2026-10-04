"""Watch the EtherCAT bus at standstill for dropouts (2026-10-03: frames lost
for ~2.5 s now and then, even with the delta virtual and off; the reel and
QEC came back in INIT / SAFE-OP).

    python tools/bus_watch.py [--hours 6] [--every 10] [--recover 20]

Every --every seconds it reads the master's lost-frame counter (SYS
EC_STATS). When it grows: log the time and the count, read every slave's
state (CODESYS daemon), then re-download (tools/safe_install.py, the delta
must already be virtual and off) to bring the bus back, up to --recover
times. Nothing moves.
"""

import argparse
import os
import subprocess
import sys
import time

import machine as mc
import topology as tp
from machine import log

HERE = os.path.dirname(os.path.abspath(__file__))
SLAVES = tp.SLAVE_NAMES
SHORT = {"ETC_SLAVE_STATE.ETC_SLAVE_OPERATIONAL": "OP", "ETC_SLAVE_STATE.ETC_SLAVE_SAVEOPERATIONAL": "SAFE-OP",
         "ETC_SLAVE_STATE.ETC_SLAVE_PREOPERATIONAL": "PRE-OP", "ETC_SLAVE_STATE.ETC_SLAVE_INIT": "INIT",
         "INT#0": "0"}


def slave_states():
    mc.rpc("logout", timeout=60)
    out = []
    for s in SLAVES:
        lines = mc.rpc("read", "IoConfig_Globals.%s.wState" % s, timeout=60)
        v = lines[-1].strip() if lines else "?"
        out.append("%s=%s" % (s.replace("ASDA_B3_E_CoE_Drive", "EAxis").replace("_1", "1").replace("_2", "2")
                              .replace("QEC_R11MP3S_V", "QEC").replace("reel_pull_motor", "reel"),
                              SHORT.get(v, v)))
    mc.rpc("logout", timeout=60)
    return " ".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6)
    ap.add_argument("--every", type=float, default=10)
    ap.add_argument("--recover", type=int, default=20)
    a = ap.parse_args()
    mc.reconnect()
    if mc.delta_mask() != 7 or mc.axis_states() != [0, 0, 0]:
        raise SystemExit("the delta must be virtual and off")
    t0 = time.time()
    drops = 0
    last = mc.sys_cmd("EC_STATS")["lost"]
    log("watch started, lost frames %d, slaves: %s" % (last, slave_states()))
    next_note = time.time() + 600
    while time.time() - t0 < a.hours * 3600:
        time.sleep(a.every)
        try:
            e = mc.sys_cmd("EC_STATS")
        except Exception as ex:
            log("EC_STATS read failed: %s" % ex)
            continue
        if e["lost"] > last:
            drops += 1
            log("DROPOUT %d at %.1f min: lost frames %d -> %d (+%d), rx errors %d" % (
                drops, (time.time() - t0) / 60, last, e["lost"], e["lost"] - last, e["rx_err"]))
            try:
                log("   slaves: %s" % slave_states())
            except Exception as ex:
                log("   slave states unreadable: %s" % ex)
            if drops > a.recover:
                log("=== stopped: more than %d dropouts" % a.recover)
                return
            r = subprocess.run([sys.executable, "-u", os.path.join(HERE, "safe_install.py"), "--no-log"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            outl = (r.stdout + r.stderr).strip().splitlines()
            log("   re-download: %s" % (outl[-1] if outl else "?"))
            try:
                last = mc.sys_cmd("EC_STATS")["lost"]
            except Exception:
                last = 0
            continue
        last = e["lost"]
        if time.time() >= next_note:
            next_note += 600
            log("%.0f min: no dropout since the last one (lost frames %d, dropouts %d)" % (
                (time.time() - t0) / 60, last, drops))
    log("=== watch done: %.1f h, dropouts %d" % ((time.time() - t0) / 3600, drops))


if __name__ == "__main__":
    main()
