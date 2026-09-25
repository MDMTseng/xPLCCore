# -*- coding: ascii -*-
# Create the PlanCountCells method POU under PROGRAM AxisGroupSM so
# import_all can resolve the new .st file (the one retained-plan
# tape cell count behind the reel move tracker). Idempotent.

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
elif fc(asm, "PlanCountCells"):
    print("already exists: PlanCountCells")
else:
    try:
        asm.create_method("PlanCountCells", "BOOL")
        print("created method PlanCountCells : BOOL")
    except Exception as ex:
        print("create_method failed:", ex)

try:
    proj.save()
    print("saved")
except Exception as ex:
    print("save ex:", ex)
