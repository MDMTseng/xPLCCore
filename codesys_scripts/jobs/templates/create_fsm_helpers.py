# -*- coding: ascii -*-
# Create TripPowerError + TripTimeout method POUs under FUNCTION_BLOCK
# AxisGroupManager. Idempotent.

HELPERS = [
    ("TripPowerError", "BOOL"),
    ("TripTimeout",    "BOOL"),
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

agm = fc(fc(fc(fc(fc(dev, "Plc Logic"), "Application"), "Robot_FBs"), "AxisGroupManager"), "AxisGroupManager")
# AxisGroupManager FB sits at /Device/Plc Logic/Application/Robot_FBs/AxisGroupManager/AxisGroupManager
# (the inner folder is the FB itself; methods become its children).
# Walk explicitly so we get the right object:
if agm is None:
    # Try alternate path: /Device/Plc Logic/Application/Robot_FBs/AxisGroupManager
    plc = fc(dev, "Plc Logic"); app = fc(plc, "Application"); rfb = fc(app, "Robot_FBs")
    agm = fc(rfb, "AxisGroupManager")

if agm is None:
    print("AxisGroupManager FB not found")
else:
    print("found:", agm.get_name())
    for name, rt in HELPERS:
        if fc(agm, name):
            print("already exists:", name)
            continue
        try:
            agm.create_method(name, rt)
            print("created method %s : %s" % (name, rt))
        except Exception as ex:
            print("create_method", name, "failed:", ex)

try:
    proj.save()
    print("saved")
except Exception as ex:
    print("save ex:", ex)
