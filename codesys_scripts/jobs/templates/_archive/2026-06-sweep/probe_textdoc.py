# -*- coding: ascii -*-
# What methods does the IScriptTextDocument expose on this version?

proj = projects.primary

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
                td_test = getattr(o, "textual_declaration", None)
                if td_test is not None and getattr(td_test, "text", None) is not None:
                    asm = o; break
        except Exception:
            pass
    if asm: break

print("asm:", asm)
td = getattr(asm, "textual_declaration", None)
print("textual_declaration type:", type(td))
attrs = sorted(a for a in dir(td) if not a.startswith("_"))
for i in range(0, len(attrs), 4):
    print("  " + ", ".join("{:<20}".format(a) for a in attrs[i:i+4]))

# Try to set .text directly and report exact error
print("\n== try assigning td.text ==")
try:
    current = td.text
    td.text = current  # set to same value
    print("  .text assignment OK (idempotent)")
except Exception as ex:
    print("  .text assignment FAILED:", type(ex).__name__, ex)

# Probe replace signature by calling with various arg counts
print("\n== probing replace() ==")
for args in (2, 3, 4, 5):
    try:
        dummy_args = ("x", "x") + tuple([False] * (args - 2))
        td.replace(*dummy_args)
        print("  replace(*{}) OK".format(args))
    except TypeError as ex:
        print("  replace(*{}) TypeError: {}".format(args, str(ex)[:80]))
    except Exception as ex:
        print("  replace(*{}) {}: {}".format(args, type(ex).__name__, str(ex)[:80]))

# Also probe insert/clear/append
for m in ("insert", "clear", "append", "write", "set_text"):
    if hasattr(td, m):
        print("  has method:", m)
