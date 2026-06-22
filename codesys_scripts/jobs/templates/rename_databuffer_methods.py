# -*- coding: ascii -*-
# Rename FB_DataBuffer methods to PascalCase (Tier-B cleanup, pair to
# rename_ringbuf_methods.py).

RENAMES = [
    ("get_buffer",   "GetBuffer"),
    ("get_head",     "GetHead"),
    ("get_space",    "GetSpace"),
    ("move_left",    "MoveLeft"),
    ("reset_buffer", "ResetBuffer"),
    ("size",         "Size"),
    ("write_length", "WriteLength"),
    ("clear",        "Clear"),
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
fbdb = fc(cfb, "FB_DataBuffer")

if fbdb is None:
    print("FB_DataBuffer not found")
else:
    for old, new in RENAMES:
        pou = fc(fbdb, old)
        if pou is None:
            if fc(fbdb, new):
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
