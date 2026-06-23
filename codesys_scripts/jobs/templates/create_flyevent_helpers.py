# -*- coding: ascii -*-
# Create ArmFiredTrigger + TickTtl method POUs under PROGRAM AxisGroupSM
# so import_all can resolve the new .st files. Idempotent.

HELPERS = [
    ("ArmFiredTrigger", "BOOL"),
    ("TickTtl",         "BOOL"),
]

proj = projects.primary

def fc(o, n):
    try:
        for c in o.get_children():
            try:
                if c.get_name() == n:
                    return c
            except Exception:
                pass
    except Exception:
        pass
    return None

dev = None
for t in proj.get_children():
    if t.get_name() == "Device":
        dev = t
        break

asm = fc(fc(fc(fc(dev, "Plc Logic"), "Application"), "APPs"), "AxisGroupSM")
if asm is None:
    print("AxisGroupSM PROGRAM not found")
else:
    for name, rt in HELPERS:
        if fc(asm, name):
            print("already exists:", name)
            continue
        try:
            asm.create_method(name, rt)
            print("created method %s : %s" % (name, rt))
        except Exception as ex:
            print("create_method", name, "failed:", ex)

try:
    proj.save()
    print("saved")
except Exception as ex:
    print("save ex:", ex)
