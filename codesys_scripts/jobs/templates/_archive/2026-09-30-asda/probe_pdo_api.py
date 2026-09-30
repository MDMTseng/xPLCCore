# -*- coding: ascii -*-
# READ-ONLY. What the scripting API exposes for EtherCAT PDO editing on
# the EAxis0 drive (ASDA_B3_E_CoE_Drive).
#
#   PYTHONIOENCODING=utf-8 rpc.py exec --readonly --file jobs/templates/probe_pdo_api.py

NAME = "ASDA_B3_E_CoE_Drive"


def t(v):
    try:
        if isinstance(v, unicode):
            return v.encode("utf-8", "replace")
        return str(v)
    except Exception:
        return "?"


def find(o):
    try:
        if o.is_device and t(o.get_name()) == NAME:
            return o
        for c in o.get_children():
            r = find(c)
            if r:
                return r
    except Exception:
        pass
    return None


dev = None
for top in projects.primary.get_children():
    dev = find(top)
    if dev:
        break
print("device:", dev)
names = [a for a in dir(dev) if not a.startswith("_")]
print("device attrs:", ", ".join(names))
for a in names:
    if any(k in a.lower() for k in ("ether", "pdo", "ecat", "slave", "process")):
        try:
            v = getattr(dev, a)
            print("  %s -> %s" % (a, t(v)))
            print("     sub:", ", ".join(x for x in dir(v) if not x.startswith("_"))[:800])
        except Exception as e:
            print("  %s error %s" % (a, e))
# parameters that look like PDO assignment / mapping
for c in dev.connectors:
    for prm in c.host_parameters:
        pn = t(prm.name)
        if any(k in pn.lower() for k in ("pdo", "1a0", "1c13", "1c12", "mapping", "assign", "entry")) or 1879000000 <= prm.id <= 1881000000:
            print("PARAM %-30s id %s = %s" % (pn[:30], t(prm.id), t(prm.value)[:150]))
