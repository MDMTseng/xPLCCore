# -*- coding: ascii -*-
# Walk codesys_code/ on disk and push every .st file back into the
# currently-open project. Match each file to a project object by path,
# split each file on the IMPL_MARKER, and overwrite the object's
# textual_declaration / textual_implementation.
#
# After all files are applied, runs generate_code() and dumps build
# messages so we can see if anything broke.
#
# v2: the daemon saves after every mutating job, so this script no
# longer needs to (and no longer warns that it did not).

import os

# config is injected from the daemon globals (see daemon.py exec_job).
SRC_ROOT = config.source_root()
IMPL_MARKER = "(* =========== IMPLEMENTATION =========== *)"

proj = projects.primary
print("project:", proj.path)

def _tdoc(o, attr):
    v = getattr(o, attr, None)
    if v is None: return None
    # Must have a .text attribute to be a ScriptTextDocument
    if getattr(v, "text", None) is None: return None
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
    """Start at /Device/Plc Logic/ then follow parts."""
    # Locate /Device
    root = None
    for top in proj.get_children():
        try:
            if top.get_name() == "Device":
                root = top; break
        except Exception:
            pass
    if root is None:
        return None
    # Locate /Device/Plc Logic
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
        # Trim a single trailing newline from decl (the one before the marker)
        if decl.endswith("\n"):
            decl = decl[:-1]
        # Strip a single leading newline from impl
        if impl.startswith("\n"):
            impl = impl[1:]
        return decl, impl
    else:
        return text, None

def file_to_parts(file_path):
    """Turn codesys_code\\Application\\APPs\\AxisGroupSM.st into
    ["Application","APPs","AxisGroupSM"].
    Collapse <POU>/<POU>.st into one entry."""
    rel = os.path.relpath(file_path, SRC_ROOT)
    if rel.endswith(".st"):
        rel = rel[:-3]
    parts = rel.replace("\\", "/").split("/")
    if len(parts) >= 2 and parts[-1] == parts[-2]:
        parts = parts[:-1]
    return parts

# Walk the on-disk tree
files = []
for root, dirs, fnames in os.walk(SRC_ROOT):
    for fn in fnames:
        if fn.endswith(".st"):
            files.append(os.path.join(root, fn))
files.sort()
print("files to import:", len(files))

applied = 0
missing = 0
unchanged = 0
for fp in files:
    parts = file_to_parts(fp)
    obj = resolve_path(parts)
    if obj is None:
        missing += 1
        print("  [miss] no project object for", "/".join(parts), "  (", fp, ")")
        continue

    # Read on-disk source. Force UTF-8 decode since file was written as
    # UTF-8 bytes by export. Don't trust isinstance() in IronPython.
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

    changed = False
    if td is not None and td.text != new_decl:
        td.replace(0, td.length, new_decl)
        changed = True
    if new_impl is not None and ti is not None and ti.text != new_impl:
        ti.replace(0, ti.length, new_impl)
        changed = True

    if changed:
        applied += 1
        print("  [apply]", "/".join(parts))
    else:
        unchanged += 1

print("")
print("summary: applied={}  unchanged={}  missing={}".format(applied, unchanged, missing))

# Build
print("\n== generate_code ==")
try:
    from System import Guid
    system.clear_messages(Guid("{97f48d64-a2a3-4856-b640-75c046e37ea9}"))
except Exception:
    pass
for app in list(proj.find("Application", True) or []):
    try:
        app.generate_code()
    except Exception as ex:
        print("  exception:", ex)

errs = 0; warns = 0
# Match the build category by GUID. get_message_category_description()
# returns a LOCALISED string, so the old `"build" in desc.lower()` test
# matched nothing on a non-English CODESYS and silently reported every
# build as clean. Severity is safe as text: it is a .NET enum whose
# ToString() gives the member name, not a translation.
def _msg_field(m, name, default):
    # A message can point into a project that is no longer open; reading
    # its position then raises StandardError ("project handle N is
    # invalid"), which getattr's default does not catch (2026-09-25).
    try:
        return getattr(m, name, default)
    except Exception:
        return default


for m in system.get_message_objects("{97f48d64-a2a3-4856-b640-75c046e37ea9}"):
    sev = str(_msg_field(m, "severity", ""))
    txt = _msg_field(m, "text", None) or "?"
    pos = _msg_field(m, "position_text", "") or ""
    if "error" in sev.lower():
        errs += 1
        print("  [ERR] {} {}".format(txt, pos))
    elif "warning" in sev.lower():
        warns += 1

print("BUILD: errors={} warnings={}".format(errs, warns))
print("(daemon will save this project when the job returns.)")
