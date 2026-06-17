# -*- coding: ascii -*-
# Delete the dead SwapREAL method on FB_MpPacker.
# msgpack.md finding #17: PackREAL uses Swap32To directly; SwapREAL is unreferenced.

APPLY = True

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

target = ["Application", "COMM_FBs", "FB_MpPacker", "SwapREAL"]
obj = resolve(target)
print("APPLY =", APPLY)
print("target:", "/".join(target), "->", obj)

if obj is not None and APPLY:
    try:
        obj.remove()
        print("  -> removed")
    except Exception as ex:
        print("  !! remove failed:", ex)

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
