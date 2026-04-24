# -*- coding: ascii -*-
# Find the existing parsing pattern for 'F', 'ACC', 'DEA', 'JERK' in
# AxisGroupSM so we can mirror it inside the ReelGo handler.

import re
proj = projects.primary

def _text(o, attr):
    v = getattr(o, attr, None)
    return getattr(v, "text", None) if v else None

def walk(o):
    yield o
    try:
        for c in o.get_children():
            for k in walk(c):
                yield k
    except Exception:
        pass

asm = None
for top in proj.get_children():
    for o in walk(top):
        try:
            if o.get_name() == "AxisGroupSM" and _text(o, "textual_implementation") is not None:
                asm = o; break
        except Exception:
            pass
    if asm: break

impl = _text(asm, "textual_implementation")
lines = impl.splitlines()

# Find every line containing TryRead( with one of F/ACC/DEA/JERK/'Distance'
NEEDLES = ("'F'", "'ACC'", "'DEA'", "'JERK'", "'Distance'", "'Velocity'")
print("==== lines using F/ACC/DEA/JERK/Distance/Velocity names ====")
for i, ln in enumerate(lines, 1):
    if any(n in ln for n in NEEDLES):
        print("  {:4d}: {}".format(i, ln.rstrip()))

# Also find the first few TryReadLREAL / TryReadReal occurrences for reference
print("\n==== first 20 TryReadLREAL/TryReadReal/TryReadDINT occurrences ====")
count = 0
for i, ln in enumerate(lines, 1):
    if "TryReadLREAL" in ln or "TryReadReal" in ln or "TryReadDINT" in ln:
        print("  {:4d}: {}".format(i, ln.rstrip()))
        count += 1
        if count >= 20: break

# Dump MessageUnpackerFb methods used
print("\n==== distinct MessageUnpackerFb.TryRead* calls ====")
methods = set()
for ln in lines:
    for m in re.finditer(r"MessageUnpackerFb\.(TryRead\w+)", ln):
        methods.add(m.group(1))
for m in sorted(methods):
    print(" ", m)
