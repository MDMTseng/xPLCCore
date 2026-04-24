# -*- coding: ascii -*-
# Switch BufferMode on the cyclic reelMoveRelative call from BlendingHigh
# to Buffered. BlendingHigh on single-axis MC_MoveRelative is unreliable
# on DS402 drives; Buffered queues one move behind the active one without
# mid-move velocity blending.

proj = projects.primary

def walk(o):
    yield o
    try:
        for c in o.get_children():
            for k in walk(c):
                yield k
    except Exception:
        pass

asm = None
for top in proj.get_children():
    for o in walk(top):
        try:
            if o.get_name() == "AxisGroupSM":
                td = getattr(o, "textual_declaration", None)
                if td is not None and getattr(td, "text", None):
                    asm = o; break
        except Exception:
            pass
    if asm: break

ti = asm.textual_implementation
impl_old = ti.text

OLD = "BufferMode   := MC_BUFFER_MODE.BlendingHigh"
NEW = "BufferMode   := MC_BUFFER_MODE.Buffered"

if OLD not in impl_old:
    print("!! anchor not found:", OLD)
    raise SystemExit(1)

impl_new = impl_old.replace(OLD, NEW, 1)
ti.replace(0, ti.length, impl_new)
print("== impl replaced ==")
print("verified:", ti.text == impl_new)

# Build
print("\n== generate_code ==")
try:
    system.clear_messages("{97f48d64-a2a3-4856-b640-75c046e37ea9}")
except Exception:
    pass
for app in list(proj.find("Application", True) or []):
    try:
        app.generate_code()
    except Exception as ex:
        print("  exception:", ex)

errs = 0; warns = 0
for cat in system.get_message_categories():
    try:
        desc = system.get_message_category_description(cat)
    except Exception:
        desc = str(cat)
    if "build" not in desc.lower():
        continue
    for m in system.get_message_objects(cat):
        sev = str(getattr(m, "severity", ""))
        txt = getattr(m, "text", None) or str(m)
        pos = getattr(m, "position_text", "") or ""
        if "error" in sev.lower():
            errs += 1
            print("  [ERR] {} {}".format(txt, pos))
        elif "warning" in sev.lower():
            warns += 1

print("\nBUILD: errors={} warnings={}".format(errs, warns))
