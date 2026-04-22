# -*- coding: ascii -*-
# Delete POUs identified as dead code in the review.
# Preview mode is ON by default -- set APPLY = True to actually delete.
#
# Targets:
#   Application/Robot_FBs/AxisGroupManager_BB   (entire backup FB; 0 external refs)
#   Application/APPs/TCP_Server_Mult             (echo-only prototype, collides on port 8123)
#   Application/Robot_FBs/performanceTestPOU     (scratch/debug POU)
#
# After deletion, the matching .st files on disk in codesys_code/ should
# also be removed (git rm) -- this script does NOT touch disk.

APPLY = False  # flip to True to actually remove (safety default)

proj = projects.primary

def find_child(obj, name):
    try:
        for c in obj.get_children():
            try:
                if c.get_name() == name: return c
            except Exception: pass
    except Exception: pass
    return None

def resolve(parts):
    root = None
    for top in proj.get_children():
        try:
            if top.get_name() == "Device": root = top; break
        except Exception: pass
    if root is None: return None
    cur = find_child(root, "Plc Logic")
    for p in parts:
        if cur is None: return None
        cur = find_child(cur, p)
    return cur

targets = [
    ["Application", "Robot_FBs", "AxisGroupManager_BB"],
    ["Application", "APPs",      "TCP_Server_Mult"],
    ["Application", "Robot_FBs", "performanceTestPOU"],
]

print("APPLY =", APPLY)
for parts in targets:
    obj = resolve(parts)
    path = "/".join(parts)
    if obj is None:
        print("  [skip - not found]", path)
        continue
    try:
        kids = list(obj.get_children())
    except Exception:
        kids = []
    print("  [target]", path, "  children:", len(kids))
    if APPLY:
        try:
            obj.remove()
            print("    -> removed")
        except Exception as ex:
            print("    !! remove failed:", ex)

# Build after delete to confirm nothing broke.
if APPLY:
    print("\n== generate_code ==")
    try:
        system.clear_messages("{97f48d64-a2a3-4856-b640-75c046e37ea9}")
    except Exception: pass
    for app in list(proj.find("Application", True) or []):
        try: app.generate_code()
        except Exception as ex: print("  exception:", ex)
    errs = warns = 0
    for cat in system.get_message_categories():
        try: desc = system.get_message_category_description(cat)
        except Exception: desc = str(cat)
        if "build" not in desc.lower(): continue
        for m in system.get_message_objects(cat):
            sev = str(getattr(m, "severity", ""))
            txt = getattr(m, "text", None) or str(m)
            pos = getattr(m, "position_text", "") or ""
            if "error" in sev.lower():
                errs += 1
                print("  [ERR] {} {}".format(txt, pos))
            elif "warning" in sev.lower():
                warns += 1
    print("BUILD: errors={} warnings={}".format(errs, warns))
    print("(project NOT saved; review in IDE and File > Save to persist.)")
else:
    print("\n(dry run -- set APPLY=True in the template to actually delete)")
