# -*- coding: ascii -*-
# Search the project for every reference to `reelpullmotor`, plus every
# AxisGroupManager instantiation, so we can see how/where the axis is
# declared and wired.

import re
import traceback

NEEDLES = [
    re.compile(r"reelpullmotor", re.IGNORECASE),
    re.compile(r"AxisGroupManager\b", re.IGNORECASE),
]

proj = projects.primary
if proj is None:
    print("[find] no primary project open!")
    raise SystemExit(2)

print("[find] project: " + proj.path)

def _text_of(obj, attr):
    try:
        val = getattr(obj, attr, None)
    except Exception:
        return None
    if val is None:
        return None
    # ScriptTextDocument-like objects have a `text` attribute
    try:
        t = getattr(val, "text", None)
        if t is not None:
            return t
    except Exception:
        pass
    try:
        return str(val)
    except Exception:
        return None

def scan(obj, path):
    try:
        name = obj.get_name()
    except Exception:
        name = "<?>"
    full = path + "/" + name

    # Try several common "source text" attributes.
    for attr in ("textual_declaration",
                 "textual_implementation",
                 "declaration",
                 "implementation"):
        text = _text_of(obj, attr)
        if not text or not isinstance(text, str):
            continue
        for pat in NEEDLES:
            if pat.search(text):
                # Emit each matching line with 1-based line number
                print("---- match: {} [{}] pat={}".format(
                    full, attr, pat.pattern))
                lines = text.splitlines()
                for i, line in enumerate(lines, 1):
                    if pat.search(line):
                        # small context: show this line with +- 1
                        lo = max(1, i-1)
                        hi = min(len(lines), i+1)
                        for j in range(lo, hi+1):
                            marker = ">>" if j == i else "  "
                            print("  {}{:4d}: {}".format(marker, j, lines[j-1]))
                        print("")
                break  # one attr match per object is enough

    # Recurse
    try:
        children = list(obj.get_children())
    except Exception:
        children = []
    for c in children:
        scan(c, full)

try:
    for top in proj.get_children():
        scan(top, "")
except Exception:
    print("[find] walk failed:")
    print(traceback.format_exc())
print("[find] done.")
