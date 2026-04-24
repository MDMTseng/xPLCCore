# -*- coding: ascii -*-
# Find exactly how project text differs from disk text for one re-applied file.
import os
SRC_ROOT = r"C:\Users\X1\Desktop\X2.5\TCP_UI\TCP_UI\codesys_code"
IMPL_MARKER = "(* =========== IMPLEMENTATION =========== *)"
proj = projects.primary

def find_child(obj, name):
    try:
        for c in obj.get_children():
            try:
                if c.get_name() == name: return c
            except Exception: pass
    except Exception: pass
    return None

def resolve(parts):
    root = None
    for top in proj.get_children():
        try:
            if top.get_name() == "Device": root = top; break
        except Exception: pass
    cur = find_child(root, "Plc Logic")
    for p in parts:
        cur = find_child(cur, p)
        if cur is None: return None
    return cur

# Pick a small one
parts = ["Application", "COMM_FBs", "FB_RingBufferIndex", "capacity"]
obj = resolve(parts)
td = obj.textual_declaration
ti = obj.textual_implementation

proj_decl = td.text
proj_impl = ti.text if ti is not None else None

disk_path = os.path.join(SRC_ROOT, "Application", "COMM_FBs", "FB_RingBufferIndex", "capacity.st")
f = open(disk_path, "rb"); raw = f.read(); f.close()
disk_text = raw.decode("utf-8", "replace")

if IMPL_MARKER in disk_text:
    idx = disk_text.index(IMPL_MARKER)
    disk_decl = disk_text[:idx]
    disk_impl = disk_text[idx + len(IMPL_MARKER):]
    if disk_decl.endswith("\n"): disk_decl = disk_decl[:-1]
    if disk_impl.startswith("\n"): disk_impl = disk_impl[1:]
else:
    disk_decl = disk_text; disk_impl = None

print("proj_decl len:", len(proj_decl), " disk_decl len:", len(disk_decl))
print("match:", proj_decl == disk_decl)
if proj_decl != disk_decl:
    # Find first diff
    n = min(len(proj_decl), len(disk_decl))
    for i in range(n):
        if proj_decl[i] != disk_decl[i]:
            print("first decl diff at", i)
            print("  proj:", repr(proj_decl[max(0,i-10):i+20]))
            print("  disk:", repr(disk_decl[max(0,i-10):i+20]))
            break
    else:
        print("one is prefix of other; proj ends:", repr(proj_decl[-30:]))
        print("                      disk ends:", repr(disk_decl[-30:]))

if proj_impl is not None:
    print("\nproj_impl len:", len(proj_impl), " disk_impl len:", len(disk_impl) if disk_impl else 0)
    print("match:", proj_impl == disk_impl)
    if proj_impl != disk_impl:
        n = min(len(proj_impl), len(disk_impl))
        for i in range(n):
            if proj_impl[i] != disk_impl[i]:
                print("first impl diff at", i)
                print("  proj:", repr(proj_impl[max(0,i-10):i+20]))
                print("  disk:", repr(disk_impl[max(0,i-10):i+20]))
                break
        else:
            print("one is prefix of other; proj ends:", repr(proj_impl[-30:]))
            print("                      disk ends:", repr(disk_impl[-30:]))

# Also: check line endings in each
print("\nproj_decl has \\r\\n:", "\r\n" in proj_decl, "  bare \\n:", proj_decl.count("\n"), "  \\r\\n count:", proj_decl.count("\r\n"))
print("disk_decl has \\r\\n:", "\r\n" in disk_decl, "  bare \\n:", disk_decl.count("\n"), "  \\r\\n count:", disk_decl.count("\r\n"))
