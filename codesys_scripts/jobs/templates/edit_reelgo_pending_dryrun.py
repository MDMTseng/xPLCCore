# -*- coding: ascii -*-
# Dry run: show the diff for adding a 1-slot pending buffer to ReelGo, so
# a second 'ReelGo' received while the first is Busy is queued instead of
# lost.

import difflib, re
proj = projects.primary

def _tdoc(o, attr):
    v = getattr(o, attr, None)
    return v if (v is not None and getattr(v, "text", None) is not None) else None

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
            if o.get_name() == "AxisGroupSM" and _tdoc(o, "textual_declaration"):
                asm = o; break
        except Exception:
            pass
    if asm: break

decl_old = asm.textual_declaration.text
impl_old = asm.textual_implementation.text

# ---- Edit A: add pending-slot vars to declaration ----
DECL_ANCHOR = (
    "        reelGoRequest  : BOOL  := FALSE;\n"
    "        reelGoDistance : LREAL := 0;\n"
    "        reelGoVelocity : LREAL := 0;\n"
    "        reelGoAccel    : LREAL := 100;\n"
    "        reelGoDecel    : LREAL := 100;\n"
    "        reelGoJerk     : LREAL := 1000;"
)
DECL_REPL = DECL_ANCHOR + "\n" + (
    "        // Pending slot: one move queued while the primary is Busy\n"
    "        reelGoPendingSet      : BOOL  := FALSE;\n"
    "        reelGoPendingDistance : LREAL := 0;\n"
    "        reelGoPendingVelocity : LREAL := 0;\n"
    "        reelGoPendingAccel    : LREAL := 100;\n"
    "        reelGoPendingDecel    : LREAL := 100;\n"
    "        reelGoPendingJerk     : LREAL := 1000;"
)
assert DECL_ANCHOR in decl_old, "declaration anchor missing"
decl_new = decl_old.replace(DECL_ANCHOR, DECL_REPL, 1)

# ---- Edit B: ReelGo handler routes to primary or pending based on Busy ----
m = re.search(
    r"ELSIF \(CommandType = 'ReelGo'\) THEN.*?GVL\.minfo_buf_ridx\.consumeTail\(\);",
    impl_old, re.DOTALL)
assert m is not None, "ReelGo block missing"
IMPL_OLD_REELGO = m.group(0)
IMPL_NEW_REELGO = (
    "ELSIF (CommandType = 'ReelGo') THEN\n"
    "                IF reelMoveRelative.Busy THEN\n"
    "                    // primary slot is occupied; fill pending\n"
    "                    reelGoPendingDistance := MessageUnpackerFb.TryReadREAL('Distance', 4);\n"
    "                    reelGoPendingVelocity := MessageUnpackerFb.TryReadREAL('F',        1000);\n"
    "                    reelGoPendingAccel    := MessageUnpackerFb.TryReadREAL('ACC',      1000);\n"
    "                    reelGoPendingDecel    := MessageUnpackerFb.TryReadREAL('DEA',      1000);\n"
    "                    reelGoPendingJerk     := MessageUnpackerFb.TryReadREAL('JERK',     10000);\n"
    "                    reelGoPendingSet      := TRUE;\n"
    "                ELSE\n"
    "                    reelGoDistance := MessageUnpackerFb.TryReadREAL('Distance', 4);\n"
    "                    reelGoVelocity := MessageUnpackerFb.TryReadREAL('F',        1000);\n"
    "                    reelGoAccel    := MessageUnpackerFb.TryReadREAL('ACC',      1000);\n"
    "                    reelGoDecel    := MessageUnpackerFb.TryReadREAL('DEA',      1000);\n"
    "                    reelGoJerk     := MessageUnpackerFb.TryReadREAL('JERK',     10000);\n"
    "                    reelGoRequest  := TRUE;\n"
    "                END_IF;\n"
    "\n"
    "                ShouldReturn := TRUE;\n"
    "                CommandAck := TRUE;\n"
    "                GVL.minfo_buf_ridx.consumeTail();"
)
impl_mid = impl_old.replace(IMPL_OLD_REELGO, IMPL_NEW_REELGO, 1)

# ---- Edit C: cyclic driver promotes pending -> primary on completion ----
CYCLIC_OLD = (
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
CYCLIC_NEW = (
    "// --- Reel motor cyclic driver ---\n"
    "// MC_MoveRelative must be called every scan so the setpoint generator\n"
    "// advances. Primary slot executes the active move; pending slot is\n"
    "// promoted to primary as soon as the active move finishes, giving us\n"
    "// seamless back-to-back queuing (1-slot pending).\n"
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
    "        IF reelGoPendingSet THEN\n"
    "            // Promote pending -> primary. reelGoRequest=FALSE this scan,\n"
    "            // then TRUE next scan, creates the rising edge the FB needs.\n"
    "            reelGoDistance   := reelGoPendingDistance;\n"
    "            reelGoVelocity   := reelGoPendingVelocity;\n"
    "            reelGoAccel      := reelGoPendingAccel;\n"
    "            reelGoDecel      := reelGoPendingDecel;\n"
    "            reelGoJerk       := reelGoPendingJerk;\n"
    "            reelGoPendingSet := FALSE;\n"
    "            reelGoRequest    := TRUE;\n"
    "        END_IF\n"
    "    END_IF\n"
    "END_IF;\n"
)
assert CYCLIC_OLD in impl_mid, "cyclic driver block missing (previous edit not applied?)"
impl_new = impl_mid.replace(CYCLIC_OLD, CYCLIC_NEW, 1)

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
