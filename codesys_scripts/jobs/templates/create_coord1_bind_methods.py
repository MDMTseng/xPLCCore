# -*- coding: ascii -*-
# Create the Coord1CommitBind / Coord1LatchWindowError method POUs under
# PROGRAM AxisGroupSM so import_all can resolve the new .st files (B3
# fix, single bind-commit + single error-snapshot latch for the SYS and
# FlyEvent COORD1 paths). Idempotent. Full declarations (VAR_INPUT /
# VAR_OUTPUT) are pushed by import_all afterwards.

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
    for name in ("Coord1CommitBind", "Coord1LatchWindowError"):
        if fc(asm, name):
            print("already exists:", name)
            continue
        try:
            asm.create_method(name, "BOOL")
            print("created method %s : BOOL" % name)
        except Exception as ex:
            print("create_method %s failed:" % name, ex)

try:
    proj.save()
    print("saved")
except Exception as ex:
    print("save ex:", ex)
