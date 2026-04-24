# -*- coding: ascii -*-
# (a) Find every line that calls AxisGroupManagerFb (plain invocation vs
#     method call) -- tells us whether the FB's cyclic body is being run.
# (b) Find the boundaries of the CommandType dispatch block.
# (c) Show the last 30 lines of implementation (for insertion-point choice).

import re

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
                if _text(o, "textual_declaration") is not None:
                    asm = o; break
        except Exception:
            pass
    if asm: break

impl = _text(asm, "textual_implementation")
lines = impl.splitlines()

# (a) AxisGroupManagerFb uses
print("==== AxisGroupManagerFb usage ====")
RE_FB = re.compile(r"AxisGroupManagerFb\s*(\.|\()", re.IGNORECASE)
hits = []
for i, ln in enumerate(lines, 1):
    if RE_FB.search(ln):
        hits.append(i)
        print("  line {}: {}".format(i, ln.rstrip()))
print("  -> {} references".format(len(hits)))

# (b) CommandType dispatch boundary
print("\n==== CommandType dispatch boundary ====")
cmd_lines = [i+1 for i, ln in enumerate(lines) if "CommandType" in ln]
print("  first CommandType mention: line", cmd_lines[0] if cmd_lines else None)
print("  last  CommandType mention: line", cmd_lines[-1] if cmd_lines else None)

# Find IF / ELSIF / END_IF around the last cmd_lines -- crude but useful
if cmd_lines:
    start = max(1, cmd_lines[0] - 5)
    print("\n  -- first CommandType context ({}-{}) --".format(start, cmd_lines[0]+2))
    for i in range(start, cmd_lines[0]+3):
        print("    {:4d}: {}".format(i, lines[i-1]))
    end = min(len(lines), cmd_lines[-1] + 20)
    print("\n  -- last CommandType + trailing ({}-{}) --".format(cmd_lines[-1], end))
    for i in range(cmd_lines[-1], end+1):
        print("    {:4d}: {}".format(i, lines[i-1]))

# (c) Last 30 lines
print("\n==== tail of implementation ({}-{}) ====".format(max(1, len(lines)-30), len(lines)))
for i in range(max(1, len(lines)-30), len(lines)+1):
    print("  {:4d}: {}".format(i, lines[i-1]))
