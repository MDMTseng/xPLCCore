"""SyncOffset sweep with the drive-demand metric (ASDA stale-target
investigation, doc_review/asda_stale_target_2026-09-30.md section 7c).

For each value: set the master's SyncOffset (jobs/templates/set_ec_sync_offset.py),
download safely, make the delta real, and run a 1 min single-joint pulse
test (EAxis0 +-1.5 deg at 0.1 deg/s, FSM Powered, no homing). Prints one
JSON line per value: late % (the drive one cycle behind its normal lag),
d2 max, EasyCAT frame arrival. Ends back at 50.

    python tools/sync_offset_sweep.py 30 40 50 --owner-ok

Values above 50 are refused: 60 / 70 wedged the PLC's EtherCAT layer
(2026-09-30).
"""

import argparse
import os
import re

import machine as mc
from machine import log
import stale_suite as ss

JOB = os.path.join(mc.REPO, "codesys_scripts", "jobs", "templates", "set_ec_sync_offset.py")


def set_sync_offset(v):
    s = open(JOB).read()
    s = re.sub(r'^WANT = "\d+"', 'WANT = "%d"' % v, s, flags=re.M)
    open(JOB, "w").write(s)
    out = mc.rpc("exec", "--file", "jobs/templates/set_ec_sync_offset.py", timeout=300)
    log(" ".join(l for l in out if "SyncOffset" in l) or out[-1:])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("values", nargs="+", type=int)
    ap.add_argument("--owner-ok", action="store_true", help="the owner OK'd moving the real delta")
    a = ap.parse_args()
    bad = [v for v in a.values if v > mc.SYNC_OFFSET_MAX]
    if bad:
        raise SystemExit("REFUSED: SyncOffset %s > %d wedges the PLC's EtherCAT layer" % (bad, mc.SYNC_OFFSET_MAX))
    mc.require_owner_ok(a.owner_ok)
    ok, ec = mc.bus_up()
    if not ok:
        raise SystemExit("EtherCAT is not up: %s" % ec)
    try:
        for i, v in enumerate(a.values):
            log("=== SyncOffset", v)
            set_sync_offset(v)
            mc.safe_install(read_log=(i == 0))
            mc.set_delta(real=True)
            ss.pulse(60)
            ss.result("SyncOffset %d" % v)
    finally:
        if a.values[-1] != 50:
            log("=== back to SyncOffset 50")
            set_sync_offset(50)
            mc.safe_install(read_log=False)


if __name__ == "__main__":
    main()
