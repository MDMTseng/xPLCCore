# -*- coding: ascii -*-
# DRY RUN. Computes the 3 edits to AxisGroupSM (declaration + ReelGo branch
# + new cyclic driver) and prints the resulting diffs. Does NOT modify the
# project. Run the _apply version once you've reviewed this output.

import difflib

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

decl_old = _text_obj(asm, "textual_declaration").text
impl_old = _text_obj(asm, "textual_implementation").text

# --- Edit 1: declaration -- inject request-pattern vars after reelMoveRelative line ---
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
    print("!! could not locate declaration anchor:", DECL_OLD_SNIPPET)
    raise SystemExit(1)
decl_new = decl_old.replace(DECL_OLD_SNIPPET, DECL_NEW_SNIPPET, 1)

# --- Edit 2: the 'ReelGo' body -- replace both reelMoveRelative calls ---
IMPL_OLD_SNIPPET = (
    "ELSIF (CommandType = 'ReelGo') THEN\n"
    "\t\t\t\t\t\n"
    "\n"
    "\t\t\t\treelMoveRelative(\n"
    "\t\t\t\t\tAxis:=reelpullmotor,\n"
    "\t\t\t\t\tExecute:=FALSE\n"
    "\n"
    "\t\t\t\t);\n"
    "\t\t\t\t\t\t\n"
    "\n"
    "\t\t\t\treelMoveRelative(\n"
    "\t\t\t\t\tAxis:=reelpullmotor,\n"
    "\t\t\t\t\tExecute:=TRUE,\n"
    "\t\t\t\t\tDistance:=4,\n"
    "\t\t\t\t\tVelocity:=3,\n"
    "\t\t\t\t\tAcceleration:=100,\n"
    "\t\t\t\t\tDeceleration:=100,\n"
    "\t\t\t\t\tJerk:=1000\n"
    "\n"
    "\t\t\t\t);\n"
    "\t\t\t\t\n"
    "\t\n"
    "\t\t\t\t\t\n"
    "                ShouldReturn := TRUE;\n"
    "                CommandAck := TRUE;\n"
    "                GVL.minfo_buf_ridx.consumeTail();"
)
IMPL_NEW_SNIPPET = (
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

# The tab-exact match may fail -- dump a check and try an alternate locator
if IMPL_OLD_SNIPPET not in impl_old:
    # Fallback: try to locate by the unique pattern "CommandType = 'ReelGo'"
    print("!! exact-tab anchor miss, falling back to regex locator")
    import re
    m = re.search(
        r"ELSIF \(CommandType = 'ReelGo'\) THEN.*?GVL\.minfo_buf_ridx\.consumeTail\(\);",
        impl_old, re.DOTALL)
    if m is None:
        print("!! could not locate ReelGo branch at all")
        raise SystemExit(1)
    IMPL_OLD_SNIPPET = m.group(0)
    print("   located {} chars; showing first/last 80 chars:".format(len(IMPL_OLD_SNIPPET)))
    print("   head:", repr(IMPL_OLD_SNIPPET[:80]))
    print("   tail:", repr(IMPL_OLD_SNIPPET[-80:]))

impl_mid = impl_old.replace(IMPL_OLD_SNIPPET, IMPL_NEW_SNIPPET, 1)

# --- Edit 3: append cyclic reelMoveRelative driver ---
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


def unified(name, a, b):
    return "\n".join(difflib.unified_diff(
        a.splitlines(), b.splitlines(),
        fromfile=name + " (current)", tofile=name + " (new)",
        n=3, lineterm=""))

print("==== DIFF: declaration ====")
print(unified("AxisGroupSM.declaration", decl_old, decl_new))

print("\n==== DIFF: implementation ====")
print(unified("AxisGroupSM.implementation", impl_old, impl_new))

print("\n[dryrun] done. No changes applied.")
