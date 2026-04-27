# -*- coding: ascii -*-
# Discover the current SoftMotion axis-group configuration:
#   - Find the AxisGroup node under Device/Plc Logic/SoftMotion General Axis Pool (or wherever).
#   - Print its kinematic transformation, axis members (logical channel + linked axis),
#     and any auxiliary-axis slots.
#
# Used to plan adding the rotary A axis as an auxiliary linear member of the
# delta-bot axis group so it interpolates synchronously with XYZ but bypasses
# the SoftMotion +/-180 deg orientation wrap.

proj = projects.primary

def walk(o, depth=0):
    yield o, depth
    try:
        for c in o.get_children():
            for k, d in walk(c, depth + 1):
                yield k, d
    except Exception:
        pass

def safe_name(o):
    try: return o.get_name()
    except Exception: return "?"

def safe_type(o):
    try: return o.type
    except Exception:
        try: return type(o).__name__
        except Exception: return "?"

print("==== Project tree (axis-group candidates) ====")
for top in proj.get_children():
    for o, d in walk(top):
        name = safe_name(o)
        low = name.lower()
        if ("axisgroup" in low or "kinematic" in low or "tripod"  in low
            or "delta" in low or "softmotion" in low or "axispool" in low
            or "trafo" in low):
            indent = "  " * d
            tname = safe_type(o)
            print("{}{} [{}]".format(indent, name, tname))

print("\n==== Device / Plc Logic top-level children ====")
for top in proj.get_children():
    if safe_name(top) == "Device":
        for c in top.get_children():
            print("  Device/{}".format(safe_name(c)))
            if safe_name(c) == "Plc Logic":
                for cc in c.get_children():
                    print("    Plc Logic/{}".format(safe_name(cc)))
                    try:
                        for ccc in cc.get_children():
                            print("      Application/{} [{}]".format(safe_name(ccc), safe_type(ccc)))
                    except Exception:
                        pass

# Try to find ANY object whose textual_declaration mentions axis-group / kinematic
print("\n==== Objects whose decl mentions AXIS_REF_SM3 or AxisGroup / Kin_ ====")
for top in proj.get_children():
    for o, d in walk(top):
        try:
            decl = getattr(o.textual_declaration, "text", "") or ""
        except Exception:
            decl = ""
        if "AXIS_REF_SM3" in decl or "Kin_" in decl or "AxisGroup " in decl or "AxisGroup\t" in decl:
            print("  {} [{}]  decl_lines={}".format(
                safe_name(o), safe_type(o), len(decl.splitlines())))
