# -*- coding: ascii -*-
# READ-ONLY. Which task drives each fieldbus master's IO?
#
#   rpc.py exec --readonly --file jobs/templates/check_bus_tasks.py
#
# A fieldbus master's generated IEC code only executes if something
# calls it. If no bus cycle task is assigned -- neither on the device
# nor as the PLC-wide default -- the whole stack sits there: no frames,
# no errors, nothing in the PLC log. That is indistinguishable from a
# dead port unless you go looking for the assignment, which is why this
# job exists.
#
# Prints every parameter whose name mentions a task, plus the task
# configuration, for the PLC device and each bus master.

import traceback


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


def nm(o):
    try:
        return t(o.get_name())
    except Exception:
        return "<unnamed>"


proj = projects.primary

INTERESTING = ("task", "cycle", "bus")

devices = []


def scan(o):
    try:
        if o.is_device:
            devices.append(o)
    except Exception:
        pass
    try:
        for c in o.get_children():
            scan(c)
    except Exception:
        pass


for top in proj.get_children():
    scan(top)

p("devices scanned: %d" % len(devices))
p("")
p("=" * 70)
p("TASK-RELATED PARAMETERS")
p("=" * 70)

found_any = False
for o in devices:
    rows = []
    try:
        cons = list(o.connectors)
    except Exception:
        cons = []
    for ci, c in enumerate(cons):
        try:
            prms = list(c.host_parameters)
        except Exception:
            continue
        for prm in prms:
            try:
                name = t(prm.name)
            except Exception:
                continue
            low = name.lower()
            if any(k in low for k in INTERESTING):
                try:
                    val = t(prm.value)
                except Exception:
                    val = "?"
                rows.append((ci, name, val))
    if rows:
        found_any = True
        p("")
        p("%s" % nm(o))
        for ci, name, val in rows:
            p("   [con %d] %-38s = %s" % (ci, name[:38], val))

if not found_any:
    p("")
    p("(no device exposes a task parameter through host_parameters --")
    p(" the bus cycle task is then set in the PLC's own settings, or on")
    p(" the device editor page, neither of which is readable from here)")

p("")
p("=" * 70)
p("TASK CONFIGURATION")
p("=" * 70)
try:
    tcs = list(proj.find("Task Configuration", True) or [])
    if not tcs:
        p("no Task Configuration object found")
    for tc in tcs:
        for task in tc.get_children():
            p("")
            p("  task: %s" % nm(task))
            try:
                for call in task.get_children():
                    p("     calls: %s" % nm(call))
            except Exception:
                pass
except Exception:
    p(traceback.format_exc()[:500])
