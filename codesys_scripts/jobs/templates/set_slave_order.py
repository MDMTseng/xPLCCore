# -*- coding: ascii -*-
# Put the EtherCAT slaves in the device tree in wire order and make them
# non-optional, so the master addresses them by position and refuses a
# mismatch (2026-10-02). Background: the DC reference clock is the first
# DC slave in the CONFIGURED order; with EC0808DN configured first but
# wired 6th, after the drives, the ASDA drives used a stale CSP target
# 3-5 % of cycles. The slaves were Optional (found by station alias), so
# the master accepted any wire order silently. The alias check
# (DeviceIdenticationMode 1) stays: it catches swapped identical drives.
#
#   rpc.py exec --file jobs/templates/set_slave_order.py
#
# Prints the drives' PDO channel IEC addresses before and after: GVL.st
# has AT %I/%Q taps on them (TapTarget, DemandTap, ActualTap, StatusTap,
# TorqueTap, CtlTap) that must follow. Offline edit + save.

ORDER = ["EC0808DN", "QEC_R11MP3S_V", "reel_pull_motor", "ASDA_B3_E_CoE_Drive",
         "ASDA_B3_E_CoE_Drive_1", "ASDA_B3_E_CoE_Drive_2", "EasyCAT"]
OPTIONAL = "False"
DRIVES = ["ASDA_B3_E_CoE_Drive", "ASDA_B3_E_CoE_Drive_1", "ASDA_B3_E_CoE_Drive_2"]


def t(v):
    try:
        if isinstance(v, unicode):
            return v.encode("utf-8", "replace")
        return str(v)
    except Exception:
        return "?"


proj = projects.primary
master = proj.find("EtherCAT_Master_SoftMotion", True)[0]


def addrs():
    out = {}
    for name in DRIVES:
        d = proj.find(name, True)[0]
        for c in d.connectors:
            try:
                prms = list(c.host_parameters)
            except Exception:
                continue
            for p in prms:
                try:
                    m = p.io_mapping
                except Exception:
                    m = None
                if m is None:
                    continue
                try:
                    out[(name, t(p.name))] = t(m.manual_iec_address)
                except Exception:
                    pass
    return out


before = addrs()
print("order before: " + ", ".join(t(d.get_name()) for d in master.get_children(False)))
for i, name in enumerate(ORDER):
    d = proj.find(name, True)[0]
    d.move(master, i)
print("order after:  " + ", ".join(t(d.get_name()) for d in master.get_children(False)))

for name in ORDER:
    d = proj.find(name, True)[0]
    for c in d.connectors:
        try:
            prms = list(c.host_parameters)
        except Exception:
            continue
        for p in prms:
            if t(p.name) == "Optional":
                old = t(p.value)
                if old != OPTIONAL:
                    p.value = OPTIONAL
                print("%s Optional: %s -> %s" % (name, old, t(p.value)))

after = addrs()
for k in sorted(before):
    if before[k] != after.get(k):
        print("ADDR %s / %s: %s -> %s" % (k[0], k[1], before[k], after.get(k)))
print("address changes: %d of %d" % (sum(1 for k in before if before[k] != after.get(k)), len(before)))
proj.save()
print("saved")
