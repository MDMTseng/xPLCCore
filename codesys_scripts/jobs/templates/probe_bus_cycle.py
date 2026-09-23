# -*- coding: ascii -*-
# Print the bus cycle task settings the scripting API exposes, per device.
#
#   rpc.py exec --readonly --file jobs/templates/probe_bus_cycle.py
#
# Offline only: reads the project tree. No login.


def p(s):
    try:
        if isinstance(s, unicode):
            s = s.encode("utf-8", "replace")
        print(s)
    except Exception:
        try:
            print(repr(s))
        except Exception:
            pass


def t(v):
    try:
        if isinstance(v, unicode):
            return v.encode("utf-8", "replace")
        return str(v)
    except Exception:
        return "?"


proj = projects.primary


def walk(o, depth):
    try:
        is_dev = o.is_device
    except Exception:
        is_dev = False
    if is_dev:
        name = t(o.get_name())
        try:
            di = o.driver_info
        except Exception as ex:
            di = None
            p("%s%s  driver_info: <%s>" % ("  " * depth, name, t(ex)[:60]))
        if di is not None:
            attrs = [a for a in dir(di) if not a.startswith("_")]
            vals = []
            for a in attrs:
                if "task" in a.lower() or "cycle" in a.lower():
                    try:
                        vals.append("%s=%s" % (a, t(getattr(di, a))))
                    except Exception as ex:
                        vals.append("%s=<%s>" % (a, t(ex)[:30]))
            p("%s%s  %s" % ("  " * depth, name, ", ".join(vals) or "(no task attrs)"))
            if depth <= 1:
                p("%s   attrs: %s" % ("  " * depth, ", ".join(attrs)))
    try:
        for c in o.get_children():
            walk(c, depth + 1)
    except Exception:
        pass


for top in proj.get_children():
    walk(top, 0)

# the task configuration, for reference
p("")
for tc in proj.find("Task Configuration", True) or []:
    for task in tc.get_children():
        p("task: %s" % t(task.get_name()))
