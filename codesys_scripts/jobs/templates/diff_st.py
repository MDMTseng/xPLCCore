# -*- coding: ascii -*-
# READ-ONLY dry run of import_all.py.
#
#   rpc.py exec --readonly --file jobs/templates/diff_st.py
#
# Walks the .st tree on disk and reports, per object, whether importing
# would change the project -- WITHOUT changing anything.
#
# Why this exists: import_all.py applies as it walks, so the first time
# you learn what a push would overwrite is after it has overwritten it.
# When the repo and the project have diverged (a project edited in the
# IDE and never exported, say) that is a silent loss of whatever was in
# the project. Run this first.

import os

SRC_ROOT = config.source_root()
IMPL_MARKER = "(* =========== IMPLEMENTATION =========== *)"


def p(s):
    try:
        if isinstance(s, unicode):
            s = s.encode("utf-8", "replace")
        print(s)
    except Exception:
        try:
            print(repr(s))
        except Exception:
            pass


proj = projects.primary
p("project   : %s" % proj.path)
p("source    : %s" % SRC_ROOT)


def _tdoc(o, attr):
    v = getattr(o, attr, None)
    if v is None:
        return None
    if getattr(v, "text", None) is None:
        return None
    return v


def find_child(obj, name):
    try:
        for c in obj.get_children():
            try:
                if c.get_name() == name:
                    return c
            except Exception:
                pass
    except Exception:
        pass
    return None


def resolve_path(parts):
    root = None
    for top in proj.get_children():
        try:
            if top.get_name() == "Device":
                root = top
                break
        except Exception:
            pass
    if root is None:
        return None
    cur = find_child(root, "Plc Logic")
    if cur is None:
        return None
    for part in parts:
        nxt = find_child(cur, part)
        if nxt is None:
            return None
        cur = nxt
    return cur


def split_source(text):
    if IMPL_MARKER in text:
        idx = text.index(IMPL_MARKER)
        decl = text[:idx]
        impl = text[idx + len(IMPL_MARKER):]
        if decl.endswith("\n"):
            decl = decl[:-1]
        if impl.startswith("\n"):
            impl = impl[1:]
        return decl, impl
    return text, None


def file_to_parts(file_path):
    rel = os.path.relpath(file_path, SRC_ROOT)
    if rel.endswith(".st"):
        rel = rel[:-3]
    parts = rel.replace("\\", "/").split("/")
    if len(parts) >= 2 and parts[-1] == parts[-2]:
        parts = parts[:-1]
    return parts


files = []
for root, dirs, fnames in os.walk(SRC_ROOT):
    for fn in fnames:
        if fn.endswith(".st"):
            files.append(os.path.join(root, fn))
files.sort()

p("files on disk: %d" % len(files))
p("")

changed = []
same = 0
missing = []

for fp in files:
    parts = file_to_parts(fp)
    obj = resolve_path(parts)
    name = "/".join(parts)
    if obj is None:
        missing.append(name)
        continue

    f = open(fp, "rb")
    try:
        raw = f.read()
    finally:
        f.close()
    try:
        new_text = raw.decode("utf-8", "replace")
    except AttributeError:
        new_text = raw

    new_decl, new_impl = split_source(new_text)
    td = _tdoc(obj, "textual_declaration")
    ti = _tdoc(obj, "textual_implementation")

    d_diff = td is not None and td.text != new_decl
    i_diff = new_impl is not None and ti is not None and ti.text != new_impl

    if d_diff or i_diff:
        # Report sizes so a wholesale replacement is obvious at a glance.
        old_len = (len(td.text) if td is not None else 0) + \
                  (len(ti.text) if ti is not None else 0)
        new_len = len(new_decl) + (len(new_impl) if new_impl else 0)
        what = []
        if d_diff:
            what.append("decl")
        if i_diff:
            what.append("impl")
        changed.append((name, "+".join(what), old_len, new_len))
    else:
        same += 1

p("=" * 70)
p("WOULD CHANGE : %d" % len(changed))
p("identical    : %d" % same)
p("no object    : %d   (on disk but not in the project)" % len(missing))
p("=" * 70)

if changed:
    p("")
    p("%-46s %-10s %9s %9s" % ("object", "part", "in proj", "on disk"))
    p("-" * 70)
    for name, what, old_len, new_len in changed:
        delta = new_len - old_len
        p("%-46s %-10s %9d %9d  (%+d)" % (
            name[:46], what, old_len, new_len, delta))

if missing:
    p("")
    p("== on disk but no matching project object ==")
    for name in missing[:40]:
        p("  %s" % name)
    if len(missing) > 40:
        p("  ... (%d more)" % (len(missing) - 40))

p("")
if changed:
    p("Nothing was modified. If any of the above is work that exists only")
    p("in the project, export it before running import_all.py --")
    p("jobs/templates/export_all.py writes the project back out to disk.")
else:
    p("Disk and project agree. import_all.py would be a no-op.")
