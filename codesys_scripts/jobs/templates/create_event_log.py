# -*- coding: ascii -*-
# Create PRG_EventLog: 1 ms event timestamp log (GVL.EvHead ring) in
# EtherCAT_Task, after AxisGroupSM. Source of truth for the code is
# codesys_code/Application/PRG_EventLog.st (copied below); after creation
# import_all.py keeps the POU in sync with that file.
#
#   rpc.py exec --file jobs/templates/create_event_log.py
#
# Read-only with respect to the machine: it only watches state.
# Idempotent. Builds; downloading is a separate step.

from System import Guid

NAME = "PRG_EventLog"
TASK = "EtherCAT_Task"
BUILD = Guid("{97f48d64-a2a3-4856-b640-75c046e37ea9}")

# Copied from codesys_code/Application/PRG_EventLog.st (the source of truth).
DECL = """PROGRAM PRG_EventLog
VAR
    xInit       : BOOL;
    uliOutPrev  : ULINT;
    uliInPrev   : ULINT;
    uliDiff     : ULINT;
    udiMovePrev : UDINT;
    udiMove     : UDINT;
    xReelPrev   : BOOL;
    xReel       : BOOL;
    iStatePrev  : INT := -1;
    iState      : INT;
    udiNow      : UDINT;
    i           : INT;
    // This scan's events, pushed together at the end.
    n           : INT;
    abyK        : ARRAY[0..79] OF BYTE;
    audiV       : ARRAY[0..79] OF UDINT;
    udiSlot     : UDINT;
    // Arm pose sampling (kinds 11/12/13 = X/Y/Z, 0.01 mm, DINT bits in val).
    iPoseTick   : INT;
    lrX         : LREAL;
    lrY         : LREAL;
    lrZ         : LREAL;
    lrLastX     : LREAL := 1.0E9;
    lrLastY     : LREAL;
    lrLastZ     : LREAL;
END_VAR
"""

IMPL = """// Event timestamp log -- see GVL.EvHead. Runs in EtherCAT_Task after
// AxisGroupSM, so it sees this scan's outputs as AxisGroupSM left them.
udiNow := LINT_TO_UDINT(AxisGroupSM.RuntimeMs);
n := 0;

IF NOT xInit THEN
    uliOutPrev := AxisGroupSM.DigitalOutputBits;
    uliInPrev  := AxisGroupSM.DigitalInputBits_Curr;
    xInit := TRUE;
END_IF

uliDiff := AxisGroupSM.DigitalOutputBits XOR uliOutPrev;
IF uliDiff <> 0 THEN
    FOR i := 0 TO 31 DO
        IF ((SHR(uliDiff, i) AND 1) <> 0) AND (n < 80) THEN
            IF (SHR(AxisGroupSM.DigitalOutputBits, i) AND 1) <> 0 THEN abyK[n] := 1; ELSE abyK[n] := 2; END_IF
            audiV[n] := INT_TO_UDINT(i);
            n := n + 1;
        END_IF
    END_FOR
END_IF
uliOutPrev := AxisGroupSM.DigitalOutputBits;

uliDiff := AxisGroupSM.DigitalInputBits_Curr XOR uliInPrev;
IF uliDiff <> 0 THEN
    FOR i := 0 TO 15 DO
        IF ((SHR(uliDiff, i) AND 1) <> 0) AND (n < 80) THEN
            IF (SHR(AxisGroupSM.DigitalInputBits_Curr, i) AND 1) <> 0 THEN abyK[n] := 3; ELSE abyK[n] := 4; END_IF
            audiV[n] := INT_TO_UDINT(i);
            n := n + 1;
        END_IF
    END_FOR
END_IF
uliInPrev := AxisGroupSM.DigitalInputBits_Curr;

udiMove := TO_UDINT(AxisGroupSM.GroupReadPositionFb.MovementId);
IF (udiMove <> udiMovePrev) AND (n < 80) THEN
    IF udiMove <> 0 THEN abyK[n] := 5; audiV[n] := udiMove;
    ELSE abyK[n] := 6; audiV[n] := udiMovePrev; END_IF
    n := n + 1;
END_IF
udiMovePrev := udiMove;

xReel := AxisGroupSM.reelMoveRelative.Busy OR AxisGroupSM.reelMoveRelative2.Busy;
IF (xReel <> xReelPrev) AND (n < 80) THEN
    IF xReel THEN abyK[n] := 7; ELSE abyK[n] := 8; END_IF
    // reel position, 0.01 axis units, DINT bits: a viewer can tell how far
    // each move went (how many tape cells)
    audiV[n] := DINT_TO_UDINT(LREAL_TO_DINT(reelpullmotor.fActPosition * 100.0));
    n := n + 1;
END_IF
xReelPrev := xReel;

iState := TO_INT(AxisGroupSM.AxisGroupManagerFb._eState);
IF (iState <> iStatePrev) AND (n < 80) THEN
    abyK[n] := 9;
    audiV[n] := INT_TO_UDINT(iState);
    n := n + 1;
END_IF
iStatePrev := iState;

// Arm pose every 50 ms, only when it moved more than 0.05 mm, so a
// timeline viewer can place the arm (tools/sim/gantt.py top view).
iPoseTick := iPoseTick + 1;
IF (iPoseTick >= 50) AND (n <= 77) THEN
    iPoseTick := 0;
    lrX := AxisGroupSM.GroupActualPositionFb.Position.c.X;
    lrY := AxisGroupSM.GroupActualPositionFb.Position.c.Y;
    lrZ := AxisGroupSM.GroupActualPositionFb.Position.c.Z;
    IF (ABS(lrX - lrLastX) > 0.05) OR (ABS(lrY - lrLastY) > 0.05) OR (ABS(lrZ - lrLastZ) > 0.05) THEN
        abyK[n] := 11; audiV[n] := DINT_TO_UDINT(LREAL_TO_DINT(lrX * 100.0)); n := n + 1;
        abyK[n] := 12; audiV[n] := DINT_TO_UDINT(LREAL_TO_DINT(lrY * 100.0)); n := n + 1;
        abyK[n] := 13; audiV[n] := DINT_TO_UDINT(LREAL_TO_DINT(lrZ * 100.0)); n := n + 1;
        lrLastX := lrX; lrLastY := lrY; lrLastZ := lrZ;
    END_IF
END_IF

FOR i := 0 TO n - 1 DO
    udiSlot := GVL.EvHead MOD 1024;
    GVL.EvT[udiSlot]    := udiNow;
    GVL.EvKind[udiSlot] := abyK[i];
    GVL.EvVal[udiSlot]  := audiV[i];
    GVL.EvHead := GVL.EvHead + 1;
END_FOR
"""


def t(v):
    try:
        if isinstance(v, unicode):
            return v.encode("utf-8", "replace")
        return str(v)
    except Exception:
        return "?"


proj = projects.primary
app = proj.active_application

pou = None
for o in app.get_children():
    if t(o.get_name()) == NAME:
        pou = o
if pou is None:
    pou = app.create_pou(NAME, PouType.Program)
    print("created %s" % NAME)
pou.textual_declaration.replace(DECL)
pou.textual_implementation.replace(IMPL)

task = None
for tc in proj.find("Task Configuration", True) or []:
    for tk in tc.get_children():
        if t(tk.get_name()) == TASK:
            task = tk
calls = [t(getattr(c, "name", c)) for c in task.pous]
if NAME not in calls:
    task.pous.add(NAME)
    print("added %s to %s (was %s)" % (NAME, TASK, calls))

system.clear_messages(BUILD)
app.generate_code()
errs = 0
for m in system.get_message_objects(BUILD):
    if "error" in str(getattr(m, "severity", "")).lower():
        errs += 1
        print("  [ERR] %s" % t(getattr(m, "text", m)))
print("build errors: %d" % errs)
