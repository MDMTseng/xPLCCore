# -*- coding: ascii -*-
# Set the EtherCAT bus cycle: EtherCAT_Task interval, the master's
# MasterCycleTime and every slave's DC sync0 / sync1 cycle time. Offline
# edit + save; deploy with `rpc.py install --on-site` with the drives off.
#
#   rpc.py exec --file jobs/templates/set_bus_cycle.py
#
# CYCLE_US 1000 is the machine's normal cycle. 10000 was a trial
# (2026-10-01): no stale target at all in 60 s of Z strokes. 2000 was a trial
# (2026-09-30): the ASDA drives used a stale target ~2x/s at 1 ms while
# the PLC, the master and a DC-synced EasyCAT at the end of the wire were
# all clean. Also change AxisGroupSM.BusPeriod (ms per scan) and
# GVL.EcNominalUs to match, and check the drives' 0x60C2 afterwards.

CYCLE_US = 10000


def t(v):
    try:
        if isinstance(v, unicode):
            return v.encode("utf-8", "replace")
        return str(v)
    except Exception:
        return "?"


proj = projects.primary
changed = []

tc = proj.find("Task Configuration", True)[0]
for task in tc.get_children(False):
    if task.get_name() == "EtherCAT_Task":
        changed.append("EtherCAT_Task interval %s %s -> %d us" % (t(task.interval), t(task.interval_unit), CYCLE_US))
        # Whole milliseconds in ms: 10000 us is refused ("The task interval
        # is invalid"), 10 ms builds (2026-10-01).
        if CYCLE_US % 1000 == 0:
            task.interval = str(CYCLE_US // 1000)
            task.interval_unit = "ms"
        else:
            task.interval = str(CYCLE_US)
            task.interval_unit = "us"


def params(o):
    try:
        for c in o.connectors:
            try:
                for prm in c.host_parameters:
                    yield prm
            except Exception:
                pass
    except Exception:
        return


def scan(o, under_master):
    try:
        name = t(o.get_name())
        is_master = o.is_device and name == "EtherCAT_Master_SoftMotion"
        if o.is_device and (is_master or under_master):
            for prm in params(o):
                pn = t(prm.name)
                if pn in ("MasterCycleTime", "DC sync0 cycletime", "DC sync1 cycletime"):
                    old = t(prm.value)
                    if old != str(CYCLE_US):
                        prm.value = str(CYCLE_US)
                        changed.append("%s %s %s -> %d" % (name, pn, old, CYCLE_US))
        for c in o.get_children():
            scan(c, under_master or is_master)
    except Exception:
        pass


for top in proj.get_children():
    scan(top, False)
for line in changed:
    print(line)
proj.save()
print("saved")
