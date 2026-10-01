# -*- coding: ascii -*-
# Set "DC sync0 shift time" on the three ASDA-B3-E slaves (EAxis0/1/2):
# their SYNC0 fires this much later than the other slaves'. Offline edit +
# save; deploy with tools/safe_install.py (drives off).
#
#   rpc.py exec --file jobs/templates/set_drive_sync_shift.py
#
# SHIFT 0 is the machine's normal setting. 2026-10-01: swept as a trial for
# the ASDA stale-target bursts (tools/sync_shift_sweep.py); the drives
# report the value in effect in CoE 0x1C32:03 (ns).

SHIFT = "0"
DRIVES = ("ASDA_B3_E_CoE_Drive", "ASDA_B3_E_CoE_Drive_1", "ASDA_B3_E_CoE_Drive_2")


def t(v):
    try:
        if isinstance(v, unicode):
            return v.encode("utf-8", "replace")
        return str(v)
    except Exception:
        return "?"


proj = projects.primary
for name in DRIVES:
    found = proj.find(name, True)
    if not found:
        raise Exception("%s not found" % name)
    dev = found[0]
    for c in dev.connectors:
        try:
            prms = list(c.host_parameters)
        except Exception:
            continue
        for prm in prms:
            if t(prm.name) == "DC sync0 shift time":
                old = t(prm.value)
                if old != SHIFT:
                    prm.value = SHIFT
                print("%s DC sync0 shift time: %s -> %s" % (name, old, t(prm.value)))
proj.save()
print("saved")
