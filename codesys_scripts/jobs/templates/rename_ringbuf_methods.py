# -*- coding: ascii -*-
# Rename FB_RingBufferIndex methods to PascalCase (Tier-B cleanup).
# Uses ScriptObject.rename(new_name).

RENAMES = [
    ("pushHead",    "PushHead"),
    ("getHead",     "GetHead"),
    ("getTail",     "GetTail"),
    ("consumeTail", "ConsumeTail"),
    ("clear",       "Clear"),
    ("capacity",    "Capacity"),
    ("size",        "Size"),
    ("space",       "Space"),
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

plc  = fc(dev, "Plc Logic")
app  = fc(plc, "Application")
cfb  = fc(app, "COMM_FBs")
fbri = fc(cfb, "FB_RingBufferIndex")

if fbri is None:
    print("FB_RingBufferIndex not found")
else:
    for old, new in RENAMES:
        pou = fc(fbri, old)
        if pou is None:
            # Maybe already renamed
            if fc(fbri, new):
                print("already renamed:", old, "->", new)
            else:
                print("not found:", old)
            continue
        try:
            pou.rename(new)
            print("renamed:", old, "->", new)
        except Exception as ex:
            print("rename failed for", old, "->", new, ":", ex)

try:
    proj.save()
    print("saved")
except Exception as ex:
    print("save ex:", ex)

try:
    r = app.build()
    print("BUILD: errors=%d warnings=%d" % (
        r.errors if hasattr(r, 'errors') else -1,
        r.warnings if hasattr(r, 'warnings') else -1))
except Exception as ex:
    print("build ex:", ex)
