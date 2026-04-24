# -*- coding: ascii -*-
# Show the AxisGroupSM declaration (full), the 20 lines around each
# reelMoveRelative call, and walk EVERY object in the project looking for
# a declaration of `reelpullmotor` in any source attr we can find.

import re
import traceback

proj = projects.primary
print("[probe] project: " + proj.path)

DECL_RE = re.compile(r"^\s*reelpullmotor\s*:", re.IGNORECASE | re.MULTILINE)
CALL_RE = re.compile(r"reelMoveRelative\s*\(", re.IGNORECASE)

SOURCE_ATTRS = (
    "textual_declaration",
    "textual_implementation",
    "declaration",
    "implementation",
    "text",              # some GVLs expose .text directly
    "pou_text",
)

def _text(obj, attr):
    try:
        v = getattr(obj, attr, None)
    except Exception:
        return None
    if v is None:
        return None
    t = getattr(v, "text", None)
    if isinstance(t, str):
        return t
    try:
        s = str(v)
        return s if isinstance(s, str) else None
    except Exception:
        return None

def _path(obj, base=""):
    try:
        return base + "/" + obj.get_name()
    except Exception:
        return base + "/?"

def show_block(lines, line_no, ctx=10, marker_line=None):
    lo = max(1, line_no - ctx)
    hi = min(len(lines), line_no + ctx)
    for i in range(lo, hi + 1):
        m = ">>" if i == (marker_line or line_no) else "  "
        print("  {}{:4d}: {}".format(m, i, lines[i-1]))

def scan(obj, base=""):
    full = _path(obj, base)
    for attr in SOURCE_ATTRS:
        text = _text(obj, attr)
        if not isinstance(text, str):
            continue
        lines = text.splitlines()

        # Declaration hits
        for m in DECL_RE.finditer(text):
            line_no = text[:m.start()].count("\n") + 1
            print("==== DECLARATION of reelpullmotor in {} [{}] ====".format(full, attr))
            show_block(lines, line_no, ctx=3)
            print("")

        # reelMoveRelative hits
        for m in CALL_RE.finditer(text):
            line_no = text[:m.start()].count("\n") + 1
            print("==== reelMoveRelative call in {} [{}] line {} ====".format(full, attr, line_no))
            show_block(lines, line_no, ctx=12)
            print("")

    # recurse
    try:
        kids = list(obj.get_children())
    except Exception:
        kids = []
    for k in kids:
        scan(k, full)

try:
    for top in proj.get_children():
        scan(top)
except Exception:
    print(traceback.format_exc())
print("[probe] done.")
