# -*- coding: ascii -*-
# Restructure: use TWO MC_MoveRelative FB instances (reelMoveRelative and
# reelMoveRelative2) so we can have one move active while another is queued
# in the axis buffer (BufferMode := Buffered). SoftMotion forbids more than
# one movement per FB instance; two instances is the PLCopen-approved way.
#
# Changes:
#   DECL: + reelMoveRelative2, + reelGoRequest2
#   IMPL: ReelGo handler dispatches to whichever FB is free
#   IMPL: cyclic driver calls both FBs, one-shot-clears both request flags

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

td = asm.textual_declaration
ti = asm.textual_implementation
decl_old = td.text
impl_old = ti.text

# ---- Declaration: inject the second FB + second request flag ----
DECL_ANCHOR = "reelMoveRelative: MC_MoveRelative;"
DECL_REPL = (
    "reelMoveRelative : MC_MoveRelative;\n"
    "        reelMoveRelative2: MC_MoveRelative;   // second instance: "
    "SoftMotion forbids >1 movement per FB"
)
if DECL_ANCHOR not in decl_old:
    print("!! decl anchor missing")
    raise SystemExit(1)
decl_mid = decl_old.replace(DECL_ANCHOR, DECL_REPL, 1)

# Add reelGoRequest2 next to reelGoRequest
DECL_REQ_ANCHOR = "reelGoRequest  : BOOL  := FALSE;"
DECL_REQ_REPL = (
    "reelGoRequest  : BOOL  := FALSE;\n"
    "        reelGoRequest2 : BOOL  := FALSE;"
)
if DECL_REQ_ANCHOR not in decl_mid:
    print("!! decl req anchor missing")
    raise SystemExit(1)
decl_new = decl_mid.replace(DECL_REQ_ANCHOR, DECL_REQ_REPL, 1)

# ---- Implementation: replace the ReelGo handler body with dispatching ----
m = re.search(
    r"ELSIF \(CommandType = 'ReelGo'\) THEN.*?GVL\.minfo_buf_ridx\.consumeTail\(\);",
    impl_old, re.DOTALL)
if m is None:
    print("!! ReelGo block not located")
    raise SystemExit(1)
IMPL_OLD_REELGO = m.group(0)
IMPL_NEW_REELGO = (
    "ELSIF (CommandType = 'ReelGo') THEN\n"
    "                reelGoDistance := MessageUnpackerFb.TryReadREAL('Distance', reelGoDistance);\n"
    "                reelGoVelocity := MessageUnpackerFb.TryReadREAL('F',        reelGoVelocity);\n"
    "                reelGoAccel    := MessageUnpackerFb.TryReadREAL('ACC',      reelGoAccel);\n"
    "                reelGoDecel    := MessageUnpackerFb.TryReadREAL('DEA',      reelGoDecel);\n"
    "                reelGoJerk     := MessageUnpackerFb.TryReadREAL('JERK',     reelGoJerk);\n"
    "\n"
    "                // Dispatch to whichever FB instance is not currently busy.\n"
    "                // If both are busy (active + queued), drop the command.\n"
    "                IF NOT reelMoveRelative.Busy THEN\n"
    "                    reelGoRequest  := TRUE;\n"
    "                ELSIF NOT reelMoveRelative2.Busy THEN\n"
    "                    reelGoRequest2 := TRUE;\n"
    "                END_IF;\n"
    "\n"
    "                ShouldReturn := TRUE;\n"
    "                CommandAck := TRUE;\n"
    "                GVL.minfo_buf_ridx.consumeTail();"
)
impl_mid = impl_old.replace(IMPL_OLD_REELGO, IMPL_NEW_REELGO, 1)

# ---- Implementation: replace the cyclic driver to call both FBs ----
m2 = re.search(
    r"// --- Reel motor cyclic driver ---.*?END_IF;\s*$",
    impl_mid, re.DOTALL)
if m2 is None:
    print("!! cyclic driver block not located")
    raise SystemExit(1)
IMPL_OLD_CYCLIC = m2.group(0)

IMPL_NEW_CYCLIC = (
    "// --- Reel motor cyclic driver ---\n"
    "// Two FB instances so we can have one move active while another is\n"
    "// queued in the axis buffer. SoftMotion rejects >1 movement per FB\n"
    "// (SMC_MORE_THAN_ONE_MOVEMENT_PER_INSTANCE) so we dispatch to whichever\n"
    "// FB is idle. BufferMode = Buffered: queued move starts when the active\n"
    "// one finishes.\n"
    "IF TRUE THEN\n"
    "    reelMoveRelative(\n"
    "        Axis         := reelpullmotor,\n"
    "        Execute      := reelGoRequest,\n"
    "        Distance     := reelGoDistance,\n"
    "        Velocity     := reelGoVelocity,\n"
    "        Acceleration := reelGoAccel,\n"
    "        Deceleration := reelGoDecel,\n"
    "        Jerk         := reelGoJerk,\n"
    "        BufferMode   := MC_BUFFER_MODE.Buffered\n"
    "    );\n"
    "    reelMoveRelative2(\n"
    "        Axis         := reelpullmotor,\n"
    "        Execute      := reelGoRequest2,\n"
    "        Distance     := reelGoDistance,\n"
    "        Velocity     := reelGoVelocity,\n"
    "        Acceleration := reelGoAccel,\n"
    "        Deceleration := reelGoDecel,\n"
    "        Jerk         := reelGoJerk,\n"
    "        BufferMode   := MC_BUFFER_MODE.Buffered\n"
    "    );\n"
    "\n"
    "    // One-shot: clear both request flags every scan so each new 'ReelGo'\n"
    "    // produces a fresh rising edge on whichever FB was selected.\n"
    "    IF reelGoRequest  THEN reelGoRequest  := FALSE; END_IF\n"
    "    IF reelGoRequest2 THEN reelGoRequest2 := FALSE; END_IF\n"
    "END_IF;"
)
impl_new = impl_mid.replace(IMPL_OLD_CYCLIC, IMPL_NEW_CYCLIC, 1)

# Apply
td.replace(0, td.length, decl_new)
ti.replace(0, ti.length, impl_new)
print("== decl replaced ==  verified:", td.text == decl_new)
print("== impl replaced ==  verified:", ti.text == impl_new)

# Show resulting blocks for sanity
print("\n---- declaration: new FB + request lines ----")
for ln in decl_new.splitlines():
    if "reelMoveRelative" in ln or "reelGoRequest" in ln:
        print("   ", ln)

print("\n---- implementation: new ReelGo block ----")
print(IMPL_NEW_REELGO)

print("\n---- implementation: new cyclic driver (tail) ----")
for ln in ti.text.splitlines()[-35:]:
    print("   ", ln)

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
