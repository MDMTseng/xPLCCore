# -*- coding: ascii -*-
# Walk the open project and write every textual object (POU, method,
# action, property, DUT, GVL, ...) to the codesys_code/ tree on disk.
#
# Layout:
#   codesys_code/
#     <folder_or_pou>/
#       ...
# Containers named "Device" and "Plc Logic" are transparent (their
# children are placed directly under codesys_code/). POUs that have
# textual children (methods/actions/properties) become a folder with a
# <PouName>.st file alongside the method files. DUTs/GVLs are single
# files.

import os

OUT_ROOT = r"C:\Users\X1\Desktop\X2.5\TCP_UI\TCP_UI\codesys_code"
SKIP_AS_FOLDER = set(["Device", "Plc Logic"])
# Names that are purely organizational inside CODESYS and aren't user code:
SKIP_ENTIRELY = set([
    "Library Manager",
    "Task Configuration",
    "Symbol Configuration",
    "Trace",
])

proj = projects.primary
print("project:", proj.path)
print("out dir:", OUT_ROOT)

def _text_of(o, attr):
    v = getattr(o, attr, None)
    return getattr(v, "text", None) if v is not None else None

# Marker comment separating declaration from implementation in merged .st
# files. Must be a full-line ST comment so the compiler ignores it if a
# file is ever imported as-is without splitting. Import splits on this
# exact line.
IMPL_MARKER = "(* =========== IMPLEMENTATION =========== *)"

def _source(o):
    decl = _text_of(o, "textual_declaration") or ""
    impl = _text_of(o, "textual_implementation")
    if impl is not None:
        sep = "" if decl.endswith("\n") else "\n"
        return decl + sep + IMPL_MARKER + "\n" + impl
    return decl

def _has_source(o):
    return (_text_of(o, "textual_declaration") is not None
            or _text_of(o, "textual_implementation") is not None)

def _sanitize(name):
    # Replace filesystem-hostile characters.
    out = []
    for ch in name:
        if ch in '<>:"/\\|?*':
            out.append("_")
        else:
            out.append(ch)
    return "".join(out)

def _write(path, content):
    d = os.path.dirname(path)
    if not os.path.isdir(d):
        os.makedirs(d)
    # ALWAYS UTF-8 encode -- IronPython's str vs unicode types are
    # inconsistent, so relying on isinstance() silently writes Latin-1 for
    # str-typed unicode content. .encode() is available on both types.
    try:
        content_bytes = content.encode("utf-8", "replace")
    except AttributeError:
        content_bytes = content  # already bytes somehow
    f = open(path, "wb")
    try:
        f.write(content_bytes)
    finally:
        f.close()

count = 0
skipped = 0

def export(obj, out_dir):
    global count, skipped
    try:
        name = obj.get_name()
    except Exception:
        return
    if name in SKIP_ENTIRELY:
        skipped += 1
        return

    try:
        children = list(obj.get_children())
    except Exception:
        children = []

    has_src = _has_source(obj)
    any_textual_kid = any(_has_source(c) for c in children) if children else False

    sanitized_name = _sanitize(name)
    if name in SKIP_AS_FOLDER:
        sub_dir = out_dir
    elif has_src and any_textual_kid:
        sub_dir = os.path.join(out_dir, sanitized_name)
    elif (not has_src) and children:
        sub_dir = os.path.join(out_dir, sanitized_name)
    else:
        sub_dir = None

    # Write own source
    if has_src:
        if sub_dir is not None:
            target = os.path.join(sub_dir, sanitized_name + ".st")
        else:
            target = os.path.join(out_dir, sanitized_name + ".st")
        _write(target, _source(obj))
        count += 1

    # Recurse
    if sub_dir is not None:
        for c in children:
            export(c, sub_dir)

# Start
if not os.path.isdir(OUT_ROOT):
    os.makedirs(OUT_ROOT)
for top in proj.get_children():
    export(top, OUT_ROOT)

print("wrote:", count, "files")
print("skipped entire subtrees:", skipped)
