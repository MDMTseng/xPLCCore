# -*- coding: ascii -*-
# READ-ONLY. Look for a way to read the PLC log (the IDE's Device -> Log
# tab) from the scripting host through .NET reflection. Lists types and
# members that mention "Log"; calls nothing that changes the PLC.
#
#   PYTHONIOENCODING=utf-8 rpc.py exec --readonly --file jobs/templates/probe_log_api.py

import clr
from System import AppDomain
from System.Reflection import BindingFlags

dev = None
for top in projects.primary.get_children():
    if top.is_device:
        dev = top
        break
od = online.create_online_device(dev)
tp = od.GetType()
print("online device type:", tp.FullName)
flags = BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public
for f in tp.GetFields(flags):
    print("  field", f.Name, f.FieldType.FullName)
for p in tp.GetProperties(flags):
    print("  prop", p.Name, p.PropertyType.FullName)

hits = []
for asm in AppDomain.CurrentDomain.GetAssemblies():
    try:
        types = asm.GetTypes()
    except Exception:
        continue
    for t in types:
        n = t.FullName or ""
        if ("Log" in n) and ("Online" in n or "Device" in n or "Plc" in n) and "Logging" not in n:
            hits.append(n)
for h in sorted(set(hits))[:80]:
    print("TYPE", h)
