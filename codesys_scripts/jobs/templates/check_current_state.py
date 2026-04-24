# -*- coding: ascii -*-
# Show the CURRENT state of AxisGroupSM so we can see whether the earlier
# edits survived the Download / IDE restart.

proj = projects.primary

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
            if o.get_name() == "AxisGroupSM":
                td = getattr(o, "textual_declaration", None)
                if td is not None and getattr(td, "text", None):
                    asm = o; break
        except Exception:
            pass
    if asm: break

decl = asm.textual_declaration.text
impl = asm.textual_implementation.text

# Does the declaration have our new vars?
print("declaration has reelGoRequest   :", "reelGoRequest" in decl)
print("declaration has reelGoDistance  :", "reelGoDistance" in decl)

# Does the impl have the cyclic driver block?
print("impl has 'Reel motor cyclic driver':", "Reel motor cyclic driver" in impl)
print("impl has reelGoRequest    :", "reelGoRequest" in impl)

# Print the tail of impl (last 40 lines) to see where it ends
lines = impl.splitlines()
print("\n---- tail of implementation (last 40 lines) ----")
lo = max(1, len(lines) - 40)
for i in range(lo, len(lines) + 1):
    print("  {:4d}: {}".format(i, lines[i-1]))

# Print the ReelGo block currently in impl
import re
m = re.search(
    r"ELSIF \(CommandType = 'ReelGo'\) THEN.*?GVL\.minfo_buf_ridx\.consumeTail\(\);",
    impl, re.DOTALL)
print("\n---- current ReelGo block ----")
if m:
    print(m.group(0))
else:
    print("(not found)")
