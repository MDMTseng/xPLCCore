# -*- coding: ascii -*-
# Enable or disable the Modbus RTU branch (Modbus_COM and everything under
# it: the client port and the SmartBowlFeeder slave). Offline edit + save;
# deploy with `rpc.py install --on-site`.
#
#   rpc.py exec --file jobs/templates/set_modbus_enabled.py
#
# Disabled 2026-09-30: the feeder link fails (RESPONSE_CRC_FAIL, 0 slaves
# communicating) and the stack's retries cost the Comm task ~1.7 ms about
# once a second. PRG_MbProbe still builds with the branch disabled (the
# device instances exist, they just do not run).

ENABLE = False
ROOT = "Modbus_COM"


def t(v):
    try:
        if isinstance(v, unicode):
            return v.encode("utf-8", "replace")
        return str(v)
    except Exception:
        return "?"


done = []


def walk(o):
    try:
        if o.is_device and t(o.get_name()) == ROOT:
            before = o.is_enabled()
            if ENABLE:
                o.enable()
            else:
                o.disable()
            done.append("%s enabled %s -> %s" % (ROOT, before, o.is_enabled()))
            return
        for c in o.get_children():
            walk(c)
    except Exception as e:
        done.append("error: %s" % e)


proj = projects.primary
for top in proj.get_children():
    walk(top)
for line in done:
    print(line)
proj.save()
print("saved")
