# -*- coding: ascii -*-
# Dump AxisGroupSM's declaration (full) and the impl slice around the
# reelMoveRelative calls, so we can design the exact edit.

proj = projects.primary

def _text(obj, attr):
    v = getattr(obj, attr, None)
    if v is None:
        return None
    return getattr(v, "text", None)

def walk_all(o):
    yield o
    try:
        for c in o.get_children():
            for k in walk_all(c):
                yield k
    except Exception:
        pass

asm = None
for top in proj.get_children():
    for o in walk_all(top):
        try:
            if o.get_name() == "AxisGroupSM":
                # Prefer the one that actually has textual_declaration
                if _text(o, "textual_declaration") is not None:
                    asm = o
                    break
        except Exception:
            pass
    if asm is not None:
        break

if asm is None:
    print("!! AxisGroupSM with textual_declaration not found")
    raise SystemExit(1)

decl = _text(asm, "textual_declaration") or ""
impl = _text(asm, "textual_implementation") or ""

print("==== DECLARATION ({} lines) ====".format(len(decl.splitlines())))
for i, line in enumerate(decl.splitlines(), 1):
    print("{:4d}: {}".format(i, line))

print("\n==== IMPLEMENTATION SIZE: {} lines ====".format(len(impl.splitlines())))

# Show the impl area around every reelMoveRelative call, plus the enclosing
# context up to the previous IF/ELSIF line (so we see what branch it is in).
impl_lines = impl.splitlines()
targets = [i+1 for i, ln in enumerate(impl_lines) if "reelMoveRelative" in ln]
print("reelMoveRelative calls at lines:", targets)

# For each call, dump 30 lines before + 30 after.
for t in targets:
    lo = max(1, t - 30)
    hi = min(len(impl_lines), t + 30)
    print("\n-- slice around line {} ({}-{}) --".format(t, lo, hi))
    for i in range(lo, hi + 1):
        marker = ">>" if i == t else "  "
        print("  {}{:4d}: {}".format(marker, i, impl_lines[i-1]))

# Also print the top of impl (first 40 lines) to see if there's a
# "header" / always-cyclic region.
print("\n==== IMPLEMENTATION top (lines 1..40) ====")
for i, line in enumerate(impl_lines[:40], 1):
    print("  {:4d}: {}".format(i, line))
