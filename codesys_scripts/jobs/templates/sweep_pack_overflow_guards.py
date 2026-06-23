"""Add overflow-latch guards to every FB_MpPacker pack helper.

  Insert at top of body (just after IMPL_MARKER):
      IF bSlotOverflowed THEN <Method> := FALSE; RETURN; END_IF

  Replace trailing `<Method> := TRUE;` with the post-pack end-check:
      IF (pSlot^.whead - pSlot^.buf) > TO_DINT(pSlot^.buf_capacity) THEN
          bSlotOverflowed := TRUE;
          <Method> := FALSE;
      ELSE
          <Method> := TRUE;
      END_IF

Run:  python codesys_scripts/jobs/templates/sweep_pack_overflow_guards.py
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
TARGET_DIR = os.path.join(ROOT, "codesys_code", "Application", "COMM_FBs", "FB_MpPacker")

TARGETS = [
    "PackKvStr",   "PackKvDint",   "PackKvLint",   "PackKvBool",   "PackKvReal",
    "PackPairStr", "PackPairDint", "PackPairLint", "PackPairBool", "PackPairReal",
    "PackElemStr", "PackElemDint",
    "PackKvMap",   "PackKvArray",
]

IMPL_MARKER = "(* =========== IMPLEMENTATION =========== *)"

for name in TARGETS:
    path = os.path.join(TARGET_DIR, name + ".st")
    if not os.path.exists(path):
        print("missing:", name)
        continue
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()

    if "bSlotOverflowed" in src:
        print("already guarded:", name)
        continue

    # Insert early-out right after IMPL_MARKER + leading comment lines.
    # Strategy: find first non-comment, non-blank line after IMPL_MARKER
    # and insert before it.
    marker_pos = src.find(IMPL_MARKER)
    if marker_pos < 0:
        print("no marker:", name); continue
    body_start = marker_pos + len(IMPL_MARKER) + 1
    # Walk past leading comment + blank lines
    cursor = body_start
    while cursor < len(src):
        # find end of line
        eol = src.find("\n", cursor)
        line = src[cursor:eol if eol >= 0 else len(src)]
        stripped = line.strip()
        if stripped == "" or stripped.startswith("//") or stripped.startswith("(*"):
            cursor = (eol + 1) if eol >= 0 else len(src)
            continue
        break

    early_out = (
        "IF bSlotOverflowed THEN " + name + " := FALSE; RETURN; END_IF\n"
    )
    src = src[:cursor] + early_out + src[cursor:]

    # Replace trailing `<name> := TRUE;` line with end-check.
    end_check = (
        "IF (pSlot^.whead - pSlot^.buf) > TO_DINT(pSlot^.buf_capacity) THEN\n"
        "    bSlotOverflowed := TRUE;\n"
        "    " + name + " := FALSE;\n"
        "ELSE\n"
        "    " + name + " := TRUE;\n"
        "END_IF\n"
    )
    pattern = re.compile(r"^" + re.escape(name) + r"\s*:=\s*TRUE\s*;\s*\n?", re.M)
    src, n = pattern.subn(end_check, src)
    if n != 1:
        print("warning: end-check sub count =", n, "for", name)

    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    print("guarded:", name)

print("done")
