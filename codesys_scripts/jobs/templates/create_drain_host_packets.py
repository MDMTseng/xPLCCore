# -*- coding: ascii -*-
# Create the DrainHostPackets method POU under PROGRAM AxisGroupSM so
# import_all can resolve the new .st file. Idempotent: skip if it
# already exists.

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

plc  = fc(dev, "Plc Logic")
app  = fc(plc, "Application")
apps = fc(app, "APPs")
asm  = fc(apps, "AxisGroupSM")

if asm is None:
    print("AxisGroupSM PROGRAM not found")
else:
    existing = fc(asm, "DrainHostPackets")
    if existing:
        print("DrainHostPackets already exists -- skipping create")
    else:
        try:
            # create_method(name, return_type)
            m = asm.create_method("DrainHostPackets", "BOOL")
            print("created method DrainHostPackets : BOOL")
        except Exception as ex:
            print("create_method failed:", ex)

try:
    proj.save()
    print("saved")
except Exception as ex:
    print("save ex:", ex)
