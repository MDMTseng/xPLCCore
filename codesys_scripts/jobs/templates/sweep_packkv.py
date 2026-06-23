"""Sweep AxisGroupSM .st files: collapse adjacent
   PackString(key) + Pack{String,DINT,LINT,Bool,REAL}(val) pairs
   into single PackKv{Str,Dint,Lint,Bool,Real}(key, val) calls.

   Run from repo root:  python codesys_scripts/jobs/templates/sweep_packkv.py
   (Pure file-edit, no rpc dependency.)
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
TARGET_DIR = os.path.join(ROOT, "codesys_code", "Application", "APPs", "AxisGroupSM")

# Match: optional leading indent captured to mirror on the result.
LEAD = r"([ \t]+)"
PACK_KEY = r"ResponsePacketPointer\^\.whead\s*:=\s*MessagePackerFb\.PackString\(pStart\s*:=\s*ResponsePacketPointer\^\.whead,\s*psValue\s*:=\s*(.+?)\);"
# Five value shapes:
VAL_SHAPES = [
    ("Str",  r"ResponsePacketPointer\^\.whead\s*:=\s*MessagePackerFb\.PackString\(pStart\s*:=\s*ResponsePacketPointer\^\.whead,\s*psValue\s*:=\s*(.+?)\);"),
    ("Dint", r"ResponsePacketPointer\^\.whead\s*:=\s*MessagePackerFb\.PackDINT\(pStart\s*:=\s*ResponsePacketPointer\^\.whead,\s*diValue\s*:=\s*(.+?)\);"),
    ("Lint", r"ResponsePacketPointer\^\.whead\s*:=\s*MessagePackerFb\.PackLINT\(pStart\s*:=\s*ResponsePacketPointer\^\.whead,\s*liValue\s*:=\s*(.+?)\);"),
    ("Bool", r"ResponsePacketPointer\^\.whead\s*:=\s*MessagePackerFb\.PackBool\(pStart\s*:=\s*ResponsePacketPointer\^\.whead,\s*bValue\s*:=\s*(.+?)\);"),
    ("Real", r"ResponsePacketPointer\^\.whead\s*:=\s*MessagePackerFb\.PackREAL\(pStart\s*:=\s*ResponsePacketPointer\^\.whead,\s*rValue\s*:=\s*(.+?)\);"),
]

def sweep_file(path):
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    orig = src
    total = 0
    for tag, val_re in VAL_SHAPES:
        pattern = LEAD + PACK_KEY + r"\s*\n" + LEAD + val_re
        def repl(m):
            indent = m.group(1)
            key_expr = m.group(2).strip()
            val_expr = m.group(4).strip()
            return f"{indent}PackKv{tag}({key_expr}, {val_expr});"
        src, n = re.subn(pattern, repl, src)
        total += n
    if src != orig:
        with open(path, "w", encoding="utf-8") as f:
            f.write(src)
    return total

def main():
    files = []
    for name in sorted(os.listdir(TARGET_DIR)):
        if name.endswith(".st"):
            files.append(os.path.join(TARGET_DIR, name))

    grand = 0
    for path in files:
        n = sweep_file(path)
        if n:
            print(f"  {os.path.relpath(path, ROOT)}: {n} pair(s) collapsed")
            grand += n
    print(f"total: {grand} pair(s)")

    # Report any remaining raw PackString sites for manual review.
    print()
    print("Remaining raw `MessagePackerFb.Pack*` writes (likely unpaired -- review):")
    raw_re = re.compile(r"ResponsePacketPointer\^\.whead\s*:=\s*MessagePackerFb\.Pack[A-Za-z]+\(")
    for path in files:
        with open(path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                if raw_re.search(line):
                    print(f"  {os.path.relpath(path, ROOT)}:{i}: {line.rstrip()[:100]}")

if __name__ == "__main__":
    sys.exit(main())
