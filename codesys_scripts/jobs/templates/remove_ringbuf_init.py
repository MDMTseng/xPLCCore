# -*- coding: ascii -*-
# Remove FB_RingBufferIndex.init method (inlined into FB_init).
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
pou  = fc(fbri, "init") if fbri else None

if pou is None:
    print("init POU not found under FB_RingBufferIndex")
else:
    try:
        pou.remove()
        print("removed FB_RingBufferIndex/init")
    except Exception as ex:
        print("remove failed:", ex)

try:
    r = app.build()
    print("BUILD: errors=%d warnings=%d" % (
        r.errors if hasattr(r, 'errors') else -1,
        r.warnings if hasattr(r, 'warnings') else -1))
except Exception as ex:
    print("build ex:", ex)

try:
    proj.save()
    print("saved")
except Exception as ex:
    print("save ex:", ex)
