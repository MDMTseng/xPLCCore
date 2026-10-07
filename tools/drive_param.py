"""Read or write ASDA-B3 drive parameters over EtherCAT SDO (PLC SYS
DRV_SDO / DRV_SDO_RESULT). Parameter Pg.nnn is object 16#2000 + g*16#100 + nnn,
subindex 0. The delta drives are EAxis0/1/2 = tools/topology.py DRIVE_STATIONS (1001/1002/1003
since the 2026-10-07 re-cabling: ASDA x3, QEC 1004, reel 1005, EC0808DN 1006, EasyCAT 1007).
A write first checks the station's identity (0x1018:01 = Delta 0x1DD) and
refuses any other device.

    python tools/drive_param.py read P3.019 P1.068 [--axes 0 1 2]
    python tools/drive_param.py write P1.068 4 [--axes 0 1 2]
    python tools/drive_param.py restore            # write back the last backup

An SDO write is stored in the drive's EEPROM (it survives a power cycle).
Before every write, the current values of the written parameters are saved
to codesys_scripts/jobs/drive_params_backup.json (the oldest value per
drive/parameter is kept, so repeated writes do not lose the original).
Some parameters only take effect with the servo off or after a power
cycle; check the manual (doc_review/asda_stale_target_2026-09-30.md).
"""

import argparse
import json
import os
import re
import time

import machine as mc
import topology as tp
from machine import log

STATIONS = tp.DRIVE_STATIONS
BACKUP = os.path.join(mc.REPO, "codesys_scripts", "jobs", "drive_params_backup.json")


def obj(name):
    m = re.match(r"^P(\d)\.(\d{1,3})$", name.upper())
    if not m:
        raise SystemExit("parameter like P3.019 expected, got %s" % name)
    return 0x2000 + int(m.group(1)) * 0x100 + int(m.group(2))


def sdo(station, index, value=None, size=4, sub=0):
    """machine.drive_sdo (shared with sync_shift_sweep; vendor check before
    the first write to a station lives there since 2026-10-05)."""
    return mc.drive_sdo(station, index, sub=sub, size=size, value=value)


def read(name, axis):
    return sdo(STATIONS[axis], obj(name))


def load_backup():
    try:
        return json.load(open(BACKUP))
    except Exception:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=("read", "write", "restore"))
    ap.add_argument("args", nargs="*")
    ap.add_argument("--axes", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--owner-ok", action="store_true",
                    help="write / restore change drive parameters (EEPROM); the PLC also needs its "
                         "maintenance gate for them, armed here with this OK (2026-10-05)")
    a = ap.parse_args()
    if a.what != "read":
        mc.require_owner_ok(a.owner_ok)
    mc.reconnect()
    if a.what == "read":
        for name in a.args:
            vals = [read(name, k) for k in a.axes]
            log("%-7s %s" % (name, "  ".join("EAxis%d %d (0x%X)" % (k, v, v & 0xFFFFFFFF) for k, v in zip(a.axes, vals))))
    elif a.what == "write":
        if len(a.args) != 2:
            raise SystemExit("write NAME VALUE")
        name, value = a.args[0], int(a.args[1], 0)
        bk = load_backup()
        for k in a.axes:
            key = "EAxis%d %s" % (k, name.upper())
            old = read(name, k)
            bk.setdefault(key, old)
            json.dump(bk, open(BACKUP, "w"), indent=1, sort_keys=True)
            sdo(STATIONS[k], obj(name), value)
            log("%s: %d -> %d (read back %d; original %d kept in the backup)" % (
                key, old, value, read(name, k), bk[key]))
    else:
        bk = load_backup()
        if not bk:
            raise SystemExit("no backup at %s" % BACKUP)
        for key, v in sorted(bk.items()):
            ax, name = key.split()
            k = int(ax[-1])
            sdo(STATIONS[k], obj(name), v)
            log("%s restored to %d (read back %d)" % (key, v, read(name, k)))


if __name__ == "__main__":
    main()
