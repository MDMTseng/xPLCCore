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

target = None
for top in proj.get_children():
    for o, d in deep(top):
        if nm(o) == "SpiderR":
            target = o; break
    if target: break

print("SpiderR found:", target, target.type)
print("\n-- dir() --")
for k in sorted(dir(target)):
    print("  ", k)

# Try to dump as XML / get raw export
print("\n-- export attempts --")
for meth_name in ("get_xml", "export_xml", "export_native", "get_data", "as_xml", "serialize", "raw_data"):
    if hasattr(target, meth_name):
        try:
            v = getattr(target, meth_name)
            if callable(v):
                r = v()
                s = repr(r)
                if len(s) > 500: s = s[:500] + "..."
                print("  {}() -> {}".format(meth_name, s))
        except Exception as e:
            print("  {}() -> err: {}".format(meth_name, e))

# Try projects.primary.export to a tmp file with this object
import os
tmp = os.path.join(os.environ.get("TEMP","C:/temp"), "spider_export.xml")
try:
    proj.export_xml([target], tmp, recursive=True, declarations_as_plaintext=True)
    print("exported to:", tmp)
    with open(tmp, "r", encoding="utf-8", errors="replace") as f:
        print(f.read()[:6000])
except Exception as e:
    print("export_xml err:", e)
