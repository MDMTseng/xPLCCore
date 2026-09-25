# -*- coding: ascii -*-
# Create the UpdateDiWatch method POU under PROGRAM AxisGroupSM so
# import_all can resolve the new .st file (digital-input change
# events, SYS DI_WATCH). Idempotent.

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
elif fc(asm, "UpdateDiWatch"):
    print("already exists: UpdateDiWatch")
else:
    try:
        asm.create_method("UpdateDiWatch", "BOOL")
        print("created method UpdateDiWatch : BOOL")
    except Exception as ex:
        print("create_method failed:", ex)

try:
    proj.save()
    print("saved")
except Exception as ex:
    print("save ex:", ex)
