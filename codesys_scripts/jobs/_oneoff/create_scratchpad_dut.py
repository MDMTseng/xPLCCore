# -*- coding: ascii -*-
# One-shot: create the Scratchpad_v1 DUT under Application/APP_COMM_FBs/
# so import_all has somewhere to write the source. After this runs once
# successfully, import_all.py picks up the file normally and overwrites
# textual_declaration with whatever's on disk.

proj = projects.primary
print("project:", proj.path)

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

# Locate Application/APP_COMM_FBs
root = None
for top in proj.get_children():
    try:
        if top.get_name() == "Device":
            root = top; break
    except Exception:
        pass
plc = find_child(root, "Plc Logic")
app = find_child(plc, "Application")
parent = find_child(app, "APP_COMM_FBs")
if parent is None:
    print("ERROR: Application/APP_COMM_FBs not found")
    raise SystemExit(1)
print("parent:", parent.get_name())

# Don't recreate if already there.
existing = find_child(parent, "Scratchpad_v1")
if existing is not None:
    print("DUT already exists:", existing.get_name())
    raise SystemExit(0)

# Probe creation APIs available on this CODESYS build.
attrs = sorted(a for a in dir(parent) if "create" in a.lower())
print("parent creation attrs:", attrs)

# Try the common variants.
dut = None
tried = []
try:
    dut = parent.create_dut("Scratchpad_v1")
    print("created via create_dut(name)")
except Exception as ex:
    tried.append(("create_dut(name)", str(ex)))

if dut is None:
    try:
        from ScriptEngine import DutType
        dut = parent.create_dut("Scratchpad_v1", DutType.Structure)
        print("created via create_dut(name, DutType.Structure)")
    except Exception as ex:
        tried.append(("create_dut+DutType", str(ex)))

if dut is None:
    try:
        dut = parent.create_struct("Scratchpad_v1")
        print("created via create_struct(name)")
    except Exception as ex:
        tried.append(("create_struct(name)", str(ex)))

if dut is None:
    print("Could not create DUT. Attempts:")
    for label, err in tried:
        print("  ", label, "->", err)
    raise SystemExit(1)

print("DUT created:", dut.get_name())
print("(now run import_all.py to populate textual_declaration)")
