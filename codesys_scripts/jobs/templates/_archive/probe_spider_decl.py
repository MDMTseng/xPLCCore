# -*- coding: ascii -*-
proj = projects.primary
def nm(o):
    try: return o.get_name()
    except: return "?"

def deep(o, d=0):
    yield o, d
    try:
        for c in o.get_children():
            for x,dd in deep(c, d+1): yield x,dd
    except: pass

for top in proj.get_children():
    for o, d in deep(top):
        if nm(o) == "SpiderR":
            print("Found SpiderR depth={}".format(d))
            try: dec = o.textual_declaration.text
            except: dec = None
            try: imp = o.textual_implementation.text
            except: imp = None
            print("decl_text:")
            if dec:
                for i, ln in enumerate(dec.splitlines(), 1):
                    print("  {:3d}: {}".format(i, ln))
            else:
                print("  <none>")
            print("impl_text:")
            if imp:
                for i, ln in enumerate(imp.splitlines(), 1):
                    print("  {:3d}: {}".format(i, ln))
            else:
                print("  <none>")
            # dir
            print("attrs:")
            for k in dir(o):
                if k.startswith("_"): continue
                try:
                    v = getattr(o, k)
                    if callable(v): continue
                    s = repr(v)
                    if len(s) > 200: s = s[:200] + "..."
                    print("  {:30s} = {}".format(k, s))
                except Exception as e:
                    pass

# also look for any object whose name contains AxisGroupGVL
print("\n==== AxisGroupGVL search ====")
for top in proj.get_children():
    for o, d in deep(top):
        n = nm(o)
        if "AxisGroupGVL" in n or "agSpiderR" in n:
            print("  {}{} [{}]".format("  "*d, n, o.type if hasattr(o,'type') else "?"))
            try:
                dec = o.textual_declaration.text
                for i, ln in enumerate(dec.splitlines(), 1):
                    print("    {:3d}: {}".format(i, ln))
            except: pass
