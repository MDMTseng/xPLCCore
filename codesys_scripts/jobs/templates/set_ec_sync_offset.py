# -*- coding: ascii -*-
# Set the EtherCAT master's SyncOffset (% of the cycle between the frame
# and SYNC0), then save the project. Offline edit only: the PLC keeps the
# old configuration until a full download (install --on-site).
#
#   rpc.py exec --file jobs/templates/set_ec_sync_offset.py
#
# 0 fires SYNC0 as the frame leaves: any master jitter lands the frame
# after SYNC0 and the drives reuse the last target (a flat cycle, then a
# double step -- seen on the ASDA scope 2026-09-30). CODESYS default 20.

MASTER = "EtherCAT_Master_SoftMotion"
WANT = "20"


def t(v):
    try:
        if isinstance(v, unicode):
            return v.encode("utf-8", "replace")
        return str(v)
    except Exception:
        return "?"


done = []


def scan(o):
    try:
        if o.is_device and t(o.get_name()) == MASTER:
            for c in o.connectors:
                try:
                    prms = list(c.host_parameters)
                except Exception:
                    continue
                for prm in prms:
                    if t(prm.name) == "SyncOffset":
                        old = t(prm.value)
                        prm.value = WANT
                        done.append((old, t(prm.value)))
    except Exception:
        pass
    try:
        for c in o.get_children():
            scan(c)
    except Exception:
        pass


proj = projects.primary
for top in proj.get_children():
    scan(top)
if done:
    for old, new in done:
        print("%s SyncOffset %s -> %s" % (MASTER, old, new))
    proj.save()
    print("saved")
else:
    print("** SyncOffset not found **")
