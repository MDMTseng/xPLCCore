# -*- coding: ascii -*-
proj = projects.primary

def find_child(obj, name):
    for c in obj.get_children():
        try:
            if c.get_name() == name: return c
        except Exception: pass
    return None

def walk(parts):
    cur = None
    for top in proj.get_children():
        if top.get_name() == "Device":
            cur = top; break
    cur = find_child(cur, "Plc Logic")
    for p in parts: cur = find_child(cur, p)
    return cur

sm = walk(["Application", "APPs", "AxisGroupSM"])
print("AxisGroupSM children before:", [c.get_name() for c in sm.get_children()])

# Inspect create_action signature
fn = sm.create_action
print("create_action repr:", repr(fn))
print("create_action doc:", getattr(fn, "__doc__", None))

# Try the simplest invocation
try:
    act = sm.create_action("PROBE_DELETE_ME")
    print("OK created:", act.get_name())
    # Inspect what got created
    print("  text decl:", repr(getattr(getattr(act, "textual_declaration", None), "text", "<none>"))[:120])
    print("  text impl:", repr(getattr(getattr(act, "textual_implementation", None), "text", "<none>"))[:120])
    # Clean up
    act.remove()
    print("  removed")
except Exception as ex:
    print("FAIL:", ex)
