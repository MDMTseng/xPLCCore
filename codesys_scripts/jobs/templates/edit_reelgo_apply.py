# -*- coding: ascii -*-
# Apply the 3 edits computed in edit_reelgo_dryrun to AxisGroupSM in the
# currently-open project. Does NOT save the project -- you can still
# CTRL+Z / close-without-save if anything looks wrong.
# After the edit, runs generate_code() and dumps build messages so we can
# confirm the edit compiles.

import re

proj = projects.primary

def _text_obj(obj, attr):
    return getattr(obj, attr, None)

def walk_all(o):
    yield o
    try:
        for c in o.get_children():
            for k in walk_all(c):
                yield k
    except Exception:
        pass

asm = None
for top in proj.get_children():
    for o in walk_all(top):
        try:
            if o.get_name() == "AxisGroupSM":
                td = _text_obj(o, "textual_declaration")
                if td is not None and getattr(td, "text", None) is not None:
                    asm = o; break
        except Exception:
            pass
    if asm: break

if asm is None:
    print("!! AxisGroupSM not found")
    raise SystemExit(1)

td = asm.textual_declaration
ti = asm.textual_implementation
decl_old = td.text
impl_old = ti.text

# ---- edits ----
DECL_OLD_SNIPPET = "reelMoveRelative: MC_MoveRelative;"
DECL_NEW_SNIPPET = (
    "reelMoveRelative: MC_MoveRelative;\n"
    "        // Request-pattern vars for cyclic reelMoveRelative\n"
    "        reelGoRequest  : BOOL  := FALSE;\n"
    "        reelGoDistance : LREAL := 0;\n"
    "        reelGoVelocity : LREAL := 0;\n"
    "        reelGoAccel    : LREAL := 100;\n"
    "        reelGoDecel    : LREAL := 100;\n"
    "        reelGoJerk     : LREAL := 1000;"
)
if DECL_OLD_SNIPPET not in decl_old:
    print("!! declaration anchor missing")
    raise SystemExit(1)
decl_new = decl_old.replace(DECL_OLD_SNIPPET, DECL_NEW_SNIPPET, 1)

m = re.search(
    r"ELSIF \(CommandType = 'ReelGo'\) THEN.*?GVL\.minfo_buf_ridx\.consumeTail\(\);",
    impl_old, re.DOTALL)
if m is None:
    print("!! ReelGo branch not located")
    raise SystemExit(1)
IMPL_OLD = m.group(0)
IMPL_NEW = (
    "ELSIF (CommandType = 'ReelGo') THEN\n"
    "                reelGoDistance := 4;\n"
    "                reelGoVelocity := 3;\n"
    "                reelGoAccel    := 100;\n"
    "                reelGoDecel    := 100;\n"
    "                reelGoJerk     := 1000;\n"
    "                reelGoRequest  := TRUE;\n"
    "\n"
    "                ShouldReturn := TRUE;\n"
    "                CommandAck := TRUE;\n"
    "                GVL.minfo_buf_ridx.consumeTail();"
)
impl_mid = impl_old.replace(IMPL_OLD, IMPL_NEW, 1)

APPEND = (
    "\n"
    "// --- Reel motor cyclic driver ---\n"
    "// MC_MoveRelative must be called every scan to advance its setpoint\n"
    "// generator; the command handler only flips reelGoRequest.\n"
    "IF TRUE THEN\n"
    "    reelMoveRelative(\n"
    "        Axis         := reelpullmotor,\n"
    "        Execute      := reelGoRequest,\n"
    "        Distance     := reelGoDistance,\n"
    "        Velocity     := reelGoVelocity,\n"
    "        Acceleration := reelGoAccel,\n"
    "        Deceleration := reelGoDecel,\n"
    "        Jerk         := reelGoJerk\n"
    "    );\n"
    "    IF reelMoveRelative.Done\n"
    "       OR reelMoveRelative.Error\n"
    "       OR reelMoveRelative.CommandAborted\n"
    "    THEN\n"
    "        reelGoRequest := FALSE;\n"
    "    END_IF\n"
    "END_IF;\n"
)
impl_new = impl_mid + APPEND

# ---- overwrite whole doc via position-based replace(offset, length, new) ----
def overwrite(tdoc, new_text):
    tdoc.replace(0, tdoc.length, new_text)

print("== applying declaration edit ==")
overwrite(td, decl_new)
print("  decl replaced")

print("== applying implementation edit ==")
overwrite(ti, impl_new)
print("  impl replaced")

# ---- verify by reading back ----
decl_back = td.text
impl_back = ti.text
print("decl ok:", (decl_back == decl_new))
print("impl ok:", (impl_back == impl_new))

# ---- generate code and dump build-category messages ----
print("\n== generate_code ==")
BUILD_CAT = "{97f48d64-a2a3-4856-b640-75c046e37ea9}"
try:
    system.clear_messages(BUILD_CAT)
except Exception:
    pass
apps = list(proj.find("Application", True) or [])
for app in apps:
    try:
        ok = app.generate_code()
        print("  generate_code({}) -> {}".format(app.get_name(), ok))
    except Exception as ex:
        print("  generate_code({}) exception: {}".format(app.get_name(), ex))

errs = 0
warns = 0
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
        print("  [{}] {} {}".format(sev, txt, pos))
        if "error" in sev.lower():
            errs += 1
        elif "warning" in sev.lower():
            warns += 1

print("\nBUILD: errors={} warnings={}".format(errs, warns))
print("(project NOT saved; use File > Save or a save job to persist)")
