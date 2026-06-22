# -*- coding: ascii -*-
# Probe rename mechanisms on a Method POU.

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
sp = fc(fbri, "space")

attempts = [
    ("rename('Space_PROBE')",    lambda o: o.rename('Space_PROBE')),
    ("name = 'Space_PROBE'",     lambda o: setattr(o, 'name', 'Space_PROBE')),
    ("Name = 'Space_PROBE'",     lambda o: setattr(o, 'Name', 'Space_PROBE')),
]

for label, fn in attempts:
    try:
        fn(sp)
        print("SUCCESS:", label, "-> new name:", sp.get_name())
        # Try to put it back
        try: sp.rename('space')
        except: pass
        try: sp.name = 'space'
        except: pass
        try: sp.Name = 'space'
        except: pass
        break
    except Exception as ex:
        print("failed:", label, "->", type(ex).__name__, str(ex))

# Also check the FB has create_method
print()
print("fbri.create_method?", hasattr(fbri, 'create_method'))
print("fbri.create_pou?",    hasattr(fbri, 'create_pou'))
print("sp.textual_declaration?", hasattr(sp, 'textual_declaration'))
print("sp.export?",              hasattr(sp, 'export'))
