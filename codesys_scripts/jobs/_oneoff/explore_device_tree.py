"""Walk the project's device tree + search device repository for
SMC_VirtualDrive (or SM3_VirtualDrive). Goal: figure out the exact
parent node where the conveyor encoder axis should land, and the
device ID we'd pass to add_device()."""

proj = projects.primary

def walk(node, depth=0):
    name = getattr(node, "get_name", lambda: "?")()
    type_id = ""
    try:
        di = node.get_device_identification()
        type_id = "  [%s/%s/%s]" % (di.type, di.id, di.version)
    except Exception:
        pass
    print("  " * depth + "- " + name + type_id)
    try:
        for child in node.get_children():
            walk(child, depth + 1)
    except Exception:
        pass

print("=== Project Device Tree ===")
for dev in proj.get_children(False):
    walk(dev)

print("\n=== Device Repository search for 'Virtual' ===")
try:
    matches = device_repository.get_all_devices()
    for d in matches:
        name = str(d.name) if hasattr(d, "name") else str(d)
        if "virtual" in name.lower() or "smc" in name.lower():
            try:
                di = d.device_identification
                print("  %s  type=%s id=%s ver=%s" % (name, di.type, di.id, di.version))
            except Exception:
                print("  %s  (no device_id)" % name)
except Exception as e:
    print("device_repository enum failed:", e)
