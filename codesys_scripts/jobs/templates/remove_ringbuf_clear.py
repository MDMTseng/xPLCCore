# -*- coding: ascii -*-
# Remove FB_RingBufferIndex.Clear method (no live callers; only
# referenced in a historical comment in CheckAxisGroupReady.st).
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

fbri = fc(fc(fc(fc(dev, "Plc Logic"), "Application"), "COMM_FBs"), "FB_RingBufferIndex")
pou = fc(fbri, "Clear") if fbri else None

if pou is None:
    print("Clear not found")
else:
    try:
        pou.remove()
        print("removed FB_RingBufferIndex/Clear")
    except Exception as ex:
        print("remove failed:", ex)

try:
    proj.save()
    print("saved")
except Exception as ex:
    print("save ex:", ex)
