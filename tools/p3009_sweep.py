"""Sweep the ASDA drives' P3.009 (communication synchronization) and measure
the stale-target rate at each setting.

P3.009 digits, lowest first (manual, P3.009):
- U: sync error range, x 10 us;
- Z: when the drive reads the PDO: SYNC0 + Z x T/10 (T = cycle). The
  default 5 puts it at T/2 -- 500 us at 1 ms, while the frame arrives
  540-614 us after SYNC0 (EasyCAT, 2026-10-01): only 40-110 us apart;
- Y: deadband, us (0 = every deviation is corrected);
- X: target value (CANopen only).

Per setting:
1. write P3.009 on EAxis0/1/2 by SDO (drive EEPROM; the original goes to
   codesys_scripts/jobs/drive_params_backup.json) and read it back;
2. restart EtherCAT with a download (machine.safe_install), drives off;
3. delta real, home, continuous Z 0 <-> -5 mm at F 5 for --seconds;
4. DEM_STATS late % and the burst events per drive.
At the end P3.009 goes back to --restore (default 0x5055).

    python tools/p3009_sweep.py --owner-ok [--values 0x5005 0x5035 0x5085 0x5A55] [--seconds 60]
"""

import argparse
import json
import time

import drive_param as dp
import machine as mc
from machine import log
from sync_shift_sweep import run_z, virtual


def write_all(value):
    for k in range(3):
        key = "EAxis%d P3.009" % k
        bk = dp.load_backup()
        bk.setdefault(key, dp.read("P3.009", k))
        json.dump(bk, open(dp.BACKUP, "w"), indent=1, sort_keys=True)
        dp.sdo(dp.STATIONS[k], dp.obj("P3.009"), value)
    rb = [dp.read("P3.009", k) for k in range(3)]
    log("  P3.009 read back: %s" % " ".join("0x%04X" % (v & 0xFFFF) for v in rb))
    return rb


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--values", nargs="+", default=["0x5005", "0x5035", "0x5085", "0x5A55"])
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--restore", default="0x5055")
    ap.add_argument("--owner-ok", action="store_true")
    a = ap.parse_args()
    mc.require_owner_ok(a.owner_ok)
    mc.reconnect()
    results = {}
    try:
        for i, v in enumerate(a.values):
            val = int(v, 0)
            log("=== P3.009 0x%04X ===" % val)
            mc.drives_off()
            rb = write_all(val)
            mc.safe_install(read_log=False)
            mc.reconnect()
            mc.set_delta(real=True)
            mc.fsm_to("Ready", timeout=180)
            r = run_z(a.seconds)
            virtual()
            results["%d %s" % (i + 1, v)] = {"readback": ["0x%04X" % (x & 0xFFFF) for x in rb], **r}
            log("  late %%: %s | burst events: %s" % (
                " / ".join("%.2f" % r["EAxis%d" % k]["late_pct"] for k in range(3)),
                " / ".join(str(r["EAxis%d" % k]["events"]) for k in range(3))))
            print(json.dumps({"run": i + 1, "P3.009": v, **results["%d %s" % (i + 1, v)]}), flush=True)
    finally:
        virtual()
        log("=== restore P3.009 %s ===" % a.restore)
        mc.drives_off()
        write_all(int(a.restore, 0))
        mc.safe_install(read_log=False)
    log("summary (P3.009: late % EAxis0/1/2 | burst events):")
    for v, r in results.items():
        log("  %s: %s | %s" % (v, " / ".join("%.2f" % r["EAxis%d" % k]["late_pct"] for k in range(3)),
                               " / ".join(str(r["EAxis%d" % k]["events"]) for k in range(3))))


if __name__ == "__main__":
    main()
