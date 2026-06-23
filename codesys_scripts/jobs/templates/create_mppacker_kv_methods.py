# -*- coding: ascii -*-
# Move msgpack composition helpers from AxisGroupSM PROGRAM into
# FB_MpPacker FB. The packer now owns a "current slot" via BindSlot;
# AxisGroupSM keeps only the slot-lifecycle helpers that touch
# reMP_info ring infrastructure.

HELPERS = [
    ("BindSlot",      "BOOL"),
    ("PackKvStr",     "BOOL"),
    ("PackKvDint",    "BOOL"),
    ("PackKvLint",    "BOOL"),
    ("PackKvBool",    "BOOL"),
    ("PackKvReal",    "BOOL"),
    ("PackPairStr",   "BOOL"),
    ("PackPairDint",  "BOOL"),
    ("PackPairLint",  "BOOL"),
    ("PackPairBool",  "BOOL"),
    ("PackPairReal",  "BOOL"),
    ("PackElemStr",   "BOOL"),
    ("PackElemDint",  "BOOL"),
    ("PackKvMap",     "BOOL"),
    ("PackKvArray",   "BOOL"),
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

mpk = fc(fc(fc(fc(dev, "Plc Logic"), "Application"), "COMM_FBs"), "FB_MpPacker")
if mpk is None:
    print("FB_MpPacker not found")
else:
    print("found:", mpk.get_name())
    for name, rt in HELPERS:
        if fc(mpk, name):
            print("already exists:", name)
            continue
        try:
            mpk.create_method(name, rt)
            print("created method %s : %s" % (name, rt))
        except Exception as ex:
            print("create_method", name, "failed:", ex)

try:
    proj.save()
    print("saved")
except Exception as ex:
    print("save ex:", ex)
