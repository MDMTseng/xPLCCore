# -*- coding: ascii -*-
# Set the EtherCAT master parameters that differ from Delta's own PLC
# (DIADesigner-AX, AX-C12; 2026-10-02 it drove our ASDA-B3-E at 1 ms with
# no stale target). Offline edit + save; deploy with tools/safe_install.py.
#
#   rpc.py exec --file jobs/templates/set_master_like_delta.py
#
# VALUES: the Delta-like setting. ORIGINAL: the machine's setting before
# this test -- put it in VALUES to go back.
# SyncWindowMonitoring 0 = off (device.xml default; the UI checkbox).

ORIGINAL = {"MasterUseLRW": "FALSE", "SyncWindowMonitoring": "1000", "SyncOffset": "50"}
VALUES = {"MasterUseLRW": "TRUE", "SyncWindowMonitoring": "0", "SyncOffset": "20"}
MASTER = "EtherCAT_Master_SoftMotion"


def t(v):
    try:
        if isinstance(v, unicode):
            return v.encode("utf-8", "replace")
        return str(v)
    except Exception:
        return "?"


proj = projects.primary
m = proj.find(MASTER, True)
if not m:
    raise Exception("%s not found" % MASTER)
m = m[0]
done = set()
for c in m.connectors:
    try:
        prms = list(c.host_parameters)
    except Exception:
        continue
    for prm in prms:
        pn = t(prm.name)
        if pn in VALUES:
            old = t(prm.value)
            if old != VALUES[pn]:
                prm.value = VALUES[pn]
            print("%s: %s -> %s" % (pn, old, t(prm.value)))
            done.add(pn)
missing = set(VALUES) - done
if missing:
    raise Exception("not found: %s" % ", ".join(sorted(missing)))
proj.save()
print("saved")
