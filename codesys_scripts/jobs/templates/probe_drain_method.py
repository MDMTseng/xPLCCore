# -*- coding: ascii -*-
# Probe DrainHostPackets method content + AxisGroupSM body to debug
# why SYS handlers stopped answering after the extract.

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
print("AxisGroupSM found:", asm is not None)

# List children of AxisGroupSM
print("AxisGroupSM children:")
for c in asm.get_children():
    try: print("  ", c.get_name())
    except: pass

dhp = fc(asm, "DrainHostPackets")
print("DrainHostPackets found:", dhp is not None)
if dhp:
    td = getattr(dhp, "textual_declaration", None)
    ti = getattr(dhp, "textual_implementation", None)
    if td:
        s = td.text
        print("--- declaration (%d chars) ---" % len(s))
        print(s[:400])
    if ti:
        s = ti.text
        print("--- impl (%d chars) ---" % len(s))
        print(s[:400])
        print("...")
        print(s[-300:])
