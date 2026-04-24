# -*- coding: ascii -*-
# Find the online-read API surface in this CODESYS version. We need to know:
#  - is there an 'online' global?
#  - does the active_application expose a way to read live values?
#  - what's the symbol/path format expected?

import traceback

def dump_dir(label, obj):
    try:
        attrs = sorted(a for a in dir(obj) if not a.startswith("_"))
    except Exception as ex:
        print("{}: dir() failed: {}".format(label, ex))
        return
    print("{}:".format(label))
    # Print in chunks of 6 for readability
    for i in range(0, len(attrs), 6):
        print("  " + ", ".join(attrs[i:i+6]))

# --- projects / project / application ---
dump_dir("dir(projects)", projects)
proj = projects.primary
print("\nproject path:", proj.path)
dump_dir("dir(proj)", proj)

print("")
try:
    app = proj.active_application
    print("active_application:", app)
    dump_dir("dir(app)", app)
except Exception as ex:
    print("active_application failed:", ex)
    app = None

# --- is there a global 'online' ? ---
print("")
try:
    _ = online
    print("'online' global EXISTS")
    dump_dir("dir(online)", online)
except NameError:
    print("'online' global does NOT exist")
except Exception as ex:
    print("online access error:", ex)

# --- device list ---
print("")
try:
    devs = list(proj.find("Device", True) or [])
    print("devices found:", len(devs))
    for d in devs:
        try:
            nm = d.get_name()
        except Exception:
            nm = "?"
        print("  device:", nm)
except Exception as ex:
    print("find(Device) failed:", ex)
    traceback.print_exc()

# --- Look for a ScriptOnline-style entry on app ---
if app is not None:
    for probe in ("online", "is_online", "current_online_application",
                  "get_online_application", "create_online_application",
                  "read_values", "monitor"):
        has = hasattr(app, probe)
        print("app.{:<28} -> {}".format(probe, has))

print("\n[discover] done.")
