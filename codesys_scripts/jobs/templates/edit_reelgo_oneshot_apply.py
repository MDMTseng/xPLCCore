# -*- coding: ascii -*-
# Flip reelGoRequest clearing from "on completion" to "every scan" so each
# 'ReelGo' produces a fresh rising edge on MC_MoveRelative.Execute. That
# makes BufferMode (Buffered / Blending*) actually work across successive
# commands.

import re
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

# Match the current completion-clear block (user-formatted, be lenient on whitespace).
OLD = re.search(
    r"(?P<head>IF reelMoveRelative\.Done\s*\n\s*OR reelMoveRelative\.Error\s*\n\s*OR reelMoveRelative\.CommandAborted\s*\n\s*THEN\s*\n\s*reelGoRequest := FALSE;\s*\n\s*END_IF)",
    impl_old)
if OLD is None:
    print("!! completion-clear block not matched")
    print("---- showing tail ----")
    for ln in impl_old.splitlines()[-25:]:
        print("   ", ln)
    raise SystemExit(1)

OLD_TEXT = OLD.group("head")
NEW_TEXT = (
    "// One-shot: clear the request every scan so a subsequent 'ReelGo'\n"
    "    // can re-raise a rising edge on Execute. BufferMode (Buffered/Blending)\n"
    "    // then tells SoftMotion how to combine it with the running move.\n"
    "    IF reelGoRequest THEN\n"
    "        reelGoRequest := FALSE;\n"
    "    END_IF"
)

impl_new = impl_old.replace(OLD_TEXT, NEW_TEXT, 1)
ti.replace(0, ti.length, impl_new)
print("== impl replaced ==")
print("verified:", ti.text == impl_new)

print("\n---- new cyclic driver block (tail of impl) ----")
for ln in ti.text.splitlines()[-20:]:
    print("   ", ln)

# Build
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
