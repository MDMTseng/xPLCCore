# -*- coding: ascii -*-
# Pin the Modbus RTU branch to the Comm task.
#
#   rpc.py exec --file jobs/templates/set_modbus_bus_cycle.py
#
# Why: the PLC-level bus cycle task (Device -> PLC Settings) points at
# task guid d109705d-..., which is none of Comm / EtherCAT_Task /
# SoftMotion_PlanningTask -- a dangling reference, most likely left over
# from a task that was deleted and recreated. Every Modbus device is on
# "unspecified" (inherit the PLC setting), so the Modbus master has no
# task to run in and never polls. The PLC log shows zero Modbus activity.
#
# Only the Modbus branch is changed. The PLC-level setting is left alone
# so the EtherCAT master is not moved.
#
# Offline edit + build only. Downloading is a separate step.

TARGET_TASK = "Comm"
MODBUS_DEVICES = ("Modbus_COM", "Modbus_Client_COM_Port")


def t(v):
    try:
        if isinstance(v, unicode):
            return v.encode("utf-8", "replace")
        return str(v)
    except Exception:
        return "?"


proj = projects.primary

task_guid = None
for tc in proj.find("Task Configuration", True) or []:
    for task in tc.get_children():
        if t(task.get_name()) == TARGET_TASK:
            task_guid = task.guid
if task_guid is None:
    print("ABORT: no task named %s" % TARGET_TASK)
    raise SystemExit(1)
print("task %s = %s" % (TARGET_TASK, task_guid))

for name in MODBUS_DEVICES:
    found = list(proj.find(name, True) or [])
    if len(found) != 1:
        print("ABORT: %s matched %d objects" % (name, len(found)))
        raise SystemExit(1)
    di = found[0].driver_info
    before = t(di.bus_cycle_task_by_guid)
    try:
        di.set_bus_cycle_task(TARGET_TASK)
    except Exception as ex:
        print("  set by name failed (%s), trying guid" % t(ex)[:80])
        di.set_bus_cycle_task(task_guid)
    di = found[0].driver_info
    print("  %-24s %s -> %s (%s)" % (name, before, t(di.bus_cycle_task_by_guid),
                                     t(di.bus_cycle_task_by_name)))

# build
from System import Guid
system.clear_messages(Guid("{97f48d64-a2a3-4856-b640-75c046e37ea9}"))
proj.active_application.generate_code()
errs = 0
for m in system.get_message_objects("{97f48d64-a2a3-4856-b640-75c046e37ea9}"):
    if "error" in str(getattr(m, "severity", "")).lower():
        errs += 1
        print("  [ERR] %s" % t(getattr(m, "text", m)))
print("build errors: %d" % errs)
if errs:
    raise SystemExit(1)
