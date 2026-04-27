# -*- coding: ascii -*-
# Idempotent bootstrap: ensure named Actions exist under AxisGroupSM
# program. Run once before import_all when adding a new Action file.
# Safe to re-run; skips actions that already exist.

ACTIONS_TO_ENSURE = [
    "InitRuntime",
    "UpdateAxisGroupState",
]

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
print("AxisGroupSM:", sm.get_name())
existing = set(c.get_name() for c in sm.get_children())
print("existing children:", sorted(existing))

for name in ACTIONS_TO_ENSURE:
    if name in existing:
        print("  [skip]", name, "already exists")
        continue
    sm.create_action(name, None)
    print("  [add ]", name)

print("after:", sorted(c.get_name() for c in sm.get_children()))
