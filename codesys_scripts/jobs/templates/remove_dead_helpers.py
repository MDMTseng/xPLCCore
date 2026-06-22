# -*- coding: ascii -*-
# Tier-A cleanup: remove three POUs that have zero call sites in the
# entire project. Verified by grep across codesys_code/ on 2026-06-23.
#
# Targets:
#   /Device/Plc Logic/Application/COMM_FBs/F_BYTE_TO_HEX_STRING
#   /Device/Plc Logic/Application/COMM_FBs/F_E_MpType_TO_STRING
#   /Device/Plc Logic/Application/COMM_FBs/FB_MpPacker/PackNil
#
# Note: the orphan F_ prefix on the first two is itself a one-off
# convention nothing else in the project follows. Method PackNil sits
# inside FB_MpPacker; the packer has no nil-emit path so the method
# was never wired up.
#
# Run as:
#   python codesys_scripts/rpc.py exec --file codesys_scripts/jobs/templates/remove_dead_helpers.py

proj = projects.primary

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

dev = None
for top in proj.get_children():
    if top.get_name() == "Device":
        dev = top
        break

plc       = find_child(dev, "Plc Logic")
app       = find_child(plc, "Application")
comm_fbs  = find_child(app, "COMM_FBs")
mp_packer = find_child(comm_fbs, "FB_MpPacker") if comm_fbs else None

removals = [
    ("COMM_FBs/F_BYTE_TO_HEX_STRING", comm_fbs, "F_BYTE_TO_HEX_STRING"),
    ("COMM_FBs/F_E_MpType_TO_STRING", comm_fbs, "F_E_MpType_TO_STRING"),
    ("COMM_FBs/FB_MpPacker/PackNil",  mp_packer, "PackNil"),
]

for label, parent, name in removals:
    if parent is None:
        print("parent missing for", label)
        continue
    pou = find_child(parent, name)
    if pou is None:
        print("not found:", label)
    else:
        try:
            pou.remove()
            print("removed:", label)
        except Exception as ex:
            print("remove failed for", label, "->", ex)

# Build to confirm no dangling references
try:
    r = app.build()
    print("BUILD: errors=%d warnings=%d" % (
        r.errors if hasattr(r, 'errors') else -1,
        r.warnings if hasattr(r, 'warnings') else -1))
except Exception as ex:
    print("build ex:", ex)

# Persist removal
try:
    proj.save()
    print("saved")
except Exception as ex:
    print("save ex:", ex)
