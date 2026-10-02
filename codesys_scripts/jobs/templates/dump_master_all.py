# -*- coding: ascii -*-
# READ-ONLY. Print every parameter of the EtherCAT master (defaults
# included) and the EtherCAT_Task settings, to compare the master setup
# with Delta's DIADesigner-AX one (2026-10-02: their PLC drives the same
# ASDA-B3-E at 1 ms without stale targets).
#
#   rpc.py exec --readonly --file jobs/templates/dump_master_all.py

MASTER = "EtherCAT_Master_SoftMotion"


def t(v):
    try:
        if isinstance(v, unicode):
            return v.encode("utf-8", "replace")
        return str(v)
    except Exception:
        return "?"


proj = projects.primary
tc = proj.find("Task Configuration", True)[0]
for task in tc.get_children(False):
    print("TASK %s" % t(task.get_name()))
    for a in ("kind_of_task", "priority", "interval", "interval_unit", "event",
              "watchdog_enabled", "watchdog_time", "watchdog_time_unit", "watchdog_sensitivity"):
        try:
            print("   %-22s %s" % (a, t(getattr(task, a))))
        except Exception:
            pass

m = proj.find(MASTER, True)
if not m:
    raise Exception("%s not found" % MASTER)
m = m[0]
for c in m.connectors:
    try:
        prms = list(c.host_parameters)
    except Exception:
        continue
    for prm in prms:
        try:
            desc = t(prm.description)
        except Exception:
            desc = ""
        print("MASTER %-6s %-40s = %-24s %s" % (t(prm.id), t(prm.name)[:40], t(prm.value)[:24], desc[:50]))
