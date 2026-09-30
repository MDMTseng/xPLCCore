# -*- coding: ascii -*-
# READ-ONLY. Task POU lists and watchdogs, and the axis group objects'
# settings (planning / bus task, etc.).
#
#   PYTHONIOENCODING=utf-8 rpc.py exec --readonly --file jobs/templates/dump_task_detail.py


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
    print("TASK %s prio %s" % (t(task.get_name()), t(task.priority)))
    try:
        print("   POUs: %s" % ", ".join(t(p) if not hasattr(p, "name") else t(p.name) for p in task.pous))
    except Exception as e:
        print("   POUs: ? %s" % e)
    try:
        w = task.watchdog
        print("   watchdog: enabled %s time %s %s sensitivity %s" % (
            t(getattr(w, "enabled", "?")), t(getattr(w, "time", "?")), t(getattr(w, "time_unit", "?")),
            t(getattr(w, "sensitivity", "?"))))
    except Exception as e:
        print("   watchdog ? %s" % e)
    for ch in task.get_children(False):
        print("   child %s" % t(ch.get_name()))


def walk(o, depth):
    try:
        name = t(o.get_name())
        tn = t(getattr(o, "type", ""))
        if "roup" in name or "Group" in tn or "SpiderR" in name or "Kin" in name:
            print("OBJ %s  (%s)" % (name, tn))
            try:
                for c in o.connectors:
                    for prm in c.host_parameters:
                        print("   %-40s = %s" % (t(prm.name)[:40], t(prm.value)[:80]))
            except Exception:
                pass
        for c in o.get_children():
            walk(c, depth + 1)
    except Exception:
        pass


for top in proj.get_children():
    walk(top, 0)
