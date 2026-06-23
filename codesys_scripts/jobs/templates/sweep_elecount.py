"""Zero-out ele_count literal initializers + remove redundant adders.

Each PackKv* helper increments ele_count by 1, so any explicit count
that matched the OLD pre-helper pack-pair count is now double-counted.

Strategy:
  * `ResponsePacketPointer^.ele_count := N;` literal  -> `:= 0;`
  * `ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + N;` -> removed entirely (helpers add now)

Blocks with manual PackMapHeader / PackArrayHeader / loop-based packs
still need their own +N adder; this script doesn't touch those (they're
the ones that look like `... + 1;  // array header` or live inside FOR loops).
Run the project build after this to catch any miscount via decode mismatch.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
TARGET_DIR = os.path.join(ROOT, "codesys_code", "Application", "APPs", "AxisGroupSM")

LITERAL = re.compile(r"(ResponsePacketPointer\^\.ele_count\s*:=\s*)\d+(\s*;)")
ADDER   = re.compile(r"^[ \t]*ResponsePacketPointer\^\.ele_count\s*:=\s*ResponsePacketPointer\^\.ele_count\s*\+\s*\d+\s*;[^\n]*\n", re.M)

SKIP_FILES = {"PackKvStr.st", "PackKvDint.st", "PackKvLint.st", "PackKvBool.st", "PackKvReal.st"}

def sweep(path):
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    orig = src
    src, n1 = LITERAL.subn(lambda m: m.group(1) + "0" + m.group(2), src)
    src, n2 = ADDER.subn("", src)
    if src != orig:
        with open(path, "w", encoding="utf-8") as f:
            f.write(src)
    return n1, n2

total_lit, total_add = 0, 0
for name in sorted(os.listdir(TARGET_DIR)):
    if not name.endswith(".st") or name in SKIP_FILES:
        continue
    path = os.path.join(TARGET_DIR, name)
    n_lit, n_add = sweep(path)
    if n_lit or n_add:
        print(f"  {name}: {n_lit} literal -> 0, {n_add} adder lines removed")
        total_lit += n_lit
        total_add += n_add
print(f"total: {total_lit} literals zeroed, {total_add} adders removed")
