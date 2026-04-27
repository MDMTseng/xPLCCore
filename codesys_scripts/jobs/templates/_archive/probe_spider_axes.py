# -*- coding: ascii -*-
# Drill into SpiderR (kinematics chain) + SoftMotion General Axis Pool to see
# kinematic transform settings and what axes exist.

proj = projects.primary

def safe_name(o):
    try: return o.get_name()
    except Exception: return "?"
def safe_type(o):
    try: return o.type
    except Exception: return "?"

def dump_attrs(o, prefix=""):
    # Inspect interesting object attributes for kinematics / axis-group nodes.
    keys = []
    for k in dir(o):
        if k.startswith("_"): continue
        keys.append(k)
    interesting = [k for k in keys if any(s in k.lower() for s in
        ("kinem", "trafo", "axis", "channel", "aux", "joint",
         "transform", "logical", "object", "name"))]
    for k in interesting:
        try:
            v = getattr(o, k)
            if callable(v): continue
            s = repr(v)
            if len(s) > 200: s = s[:200] + "..."
            print("{}{:30s} = {}".format(prefix, k, s))
        except Exception as e:
            print("{}{:30s} = <err: {}>".format(prefix, k, e))

def walk(o, depth=0, max_depth=6):
    if depth > max_depth: return
    print("{}{} [{}]".format("  "*depth, safe_name(o), safe_type(o)))
    try:
        for c in o.get_children():
            walk(c, depth+1, max_depth)
    except Exception:
        pass

device = None
for top in proj.get_children():
    if safe_name(top) == "Device":
        device = top; break

print("==== Device/SpiderR tree ====")
for c in device.get_children():
    if safe_name(c) == "SpiderR":
        walk(c, 0, 8)
        print("\n--- attrs of SpiderR root ---")
        dump_attrs(c, "  ")
        # Also dump first-level child attrs
        for cc in c.get_children():
            print("\n--- attrs of {} ---".format(safe_name(cc)))
            dump_attrs(cc, "  ")

print("\n==== Device/SoftMotion General Axis Pool ====")
for c in device.get_children():
    if safe_name(c) == "SoftMotion General Axis Pool":
        for cc in c.get_children():
            print("  axis: {} [{}]".format(safe_name(cc), safe_type(cc)))

# SpiderR may also live under PlcLogic
print("\n==== Search anywhere named SpiderR or with 'kin' attrs ====")
def deep(o, depth=0):
    yield o, depth
    try:
        for c in o.get_children():
            for k,d in deep(c, depth+1): yield k,d
    except Exception:
        pass

for top in proj.get_children():
    for o,d in deep(top):
        nm = safe_name(o).lower()
        if "spider" in nm or "tripod" in nm:
            print("  hit: {}{} [{}]".format("  "*d, safe_name(o), safe_type(o)))
