PROGRAM PRG_EventLog
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
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Event timestamp log -- see GVL.EvHead. Runs in EtherCAT_Task after
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
    audiV[n] := 0;
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

FOR i := 0 TO n - 1 DO
    udiSlot := GVL.EvHead MOD 1024;
    GVL.EvT[udiSlot]    := udiNow;
    GVL.EvKind[udiSlot] := abyK[i];
    GVL.EvVal[udiSlot]  := audiV[i];
    GVL.EvHead := GVL.EvHead + 1;
END_FOR
