# -*- coding: ascii -*-
# READ-ONLY. List the methods of the device-level online object, to find a
# way to read the runtime's log remotely. Connects, lists, disconnects.
#
#   PYTHONIOENCODING=utf-8 rpc.py exec --readonly --file jobs/templates/probe_online_device.py

dev = None
for top in projects.primary.get_children():
    if top.is_device:
        dev = top
        break
od = online.create_online_device(dev)
print("attrs:", ", ".join(a for a in dir(od) if not a.startswith("_")))
