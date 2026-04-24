# -*- coding: ascii -*-
# Replace the ReelGo handler body so it reads F/ACC/DEA/JERK/Distance from
# the incoming message (mirroring the G1-style pattern at lines 796-799).
# Keeps hardcoded defaults so existing callers still work.

import re
proj = projects.primary

def _text(o, attr):
    v = getattr(o, attr, None)
    return v if v is not None else None

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
                ti = _text(o, "textual_implementation")
                if ti is not None and getattr(ti, "text", None):
                    asm = o; break
        except Exception:
            pass
    if asm: break

ti = asm.textual_implementation
impl_old = ti.text

# Match the current ReelGo block (from the previous edit).
m = re.search(
    r"ELSIF \(CommandType = 'ReelGo'\) THEN.*?GVL\.minfo_buf_ridx\.consumeTail\(\);",
    impl_old, re.DOTALL)
if m is None:
    print("!! ReelGo block not located")
    raise SystemExit(1)

IMPL_NEW_BLOCK = (
    "ELSIF (CommandType = 'ReelGo') THEN\n"
    "                reelGoDistance := MessageUnpackerFb.TryReadREAL('Distance', 4);\n"
    "                reelGoVelocity := MessageUnpackerFb.TryReadREAL('F',        1000);\n"
    "                reelGoAccel    := MessageUnpackerFb.TryReadREAL('ACC',      1000);\n"
    "                reelGoDecel    := MessageUnpackerFb.TryReadREAL('DEA',      1000);\n"
    "                reelGoJerk     := MessageUnpackerFb.TryReadREAL('JERK',     10000);\n"
    "                reelGoRequest  := TRUE;\n"
    "\n"
    "                ShouldReturn := TRUE;\n"
    "                CommandAck := TRUE;\n"
    "                GVL.minfo_buf_ridx.consumeTail();"
)

impl_new = impl_old.replace(m.group(0), IMPL_NEW_BLOCK, 1)

# Apply
ti.replace(0, ti.length, impl_new)
print("== impl replaced ==")
print("verified:", ti.text == impl_new)

# Show the new block
print("\n---- NEW ReelGo block ----")
print(IMPL_NEW_BLOCK)

# Build to verify
print("\n== generate_code ==")
BUILD_CAT = "{97f48d64-a2a3-4856-b640-75c046e37ea9}"
try:
    system.clear_messages(BUILD_CAT)
except Exception:
    pass
for app in list(proj.find("Application", True) or []):
    try:
        app.generate_code()
    except Exception as ex:
        print("  exception:", ex)

errs = 0
warns = 0
build_msgs = []
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

# Filter: only print the summary line for warnings
for m_ in []: pass
print("BUILD: errors={} warnings={}".format(errs, warns))
