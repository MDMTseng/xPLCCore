# -*- coding: ascii -*-
# READ-ONLY. Print the Task Configuration: every task with its type,
# priority, interval, watchdog, core affinity (where exposed) and the
# POUs it calls, plus task-related parameters of the PLC device and the
# EtherCAT master and SoftMotion-related device parameters.
#
#   rpc.py exec --readonly --file jobs/templates/dump_task_config.py


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
    for a in ("kind_of_task", "priority", "interval", "interval_unit", "event", "watchdog",
              "watchdog_enabled", "watchdog_time", "watchdog_time_unit", "watchdog_sensitivity",
              "core", "affinity", "cycle_time"):
        try:
            v = getattr(task, a)
            if callable(v):
                continue
            print("   %-22s %s" % (a, t(v)))
        except Exception:
            pass
    try:
        for pou in task.pous:
            print("   POU %s" % t(pou.name))
    except Exception as e:
        print("   (pous: %s)" % e)
    try:
        print("   other attrs: %s" % ", ".join(a for a in dir(task) if not a.startswith("_"))[:600])
    except Exception:
        pass
    break_after = False

for task in tc.get_children(False):
    pass


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


def scan(o, depth):
    try:
        name = t(o.get_name())
        if o.is_device and depth <= 3:
            for prm in params(o):
                pn = t(prm.name)
                low = pn.lower()
                if any(k in low for k in ("task", "cycle", "priority", "frameat", "planning", "softmotion", "sync")):
                    print("PARAM %-30s %-36s = %s" % (name[:30], pn[:36], t(prm.value)[:60]))
        for c in o.get_children():
            scan(c, depth + 1)
    except Exception:
        pass


for top in proj.get_children():
    scan(top, 0)
