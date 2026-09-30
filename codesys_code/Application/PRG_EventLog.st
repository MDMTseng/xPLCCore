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
    // Arm pose sampling (kinds 11/12/13 = X/Y/Z, 0.01 mm, DINT bits in val).
    iPoseTick   : INT;
    lrX         : LREAL;
    lrY         : LREAL;
    lrZ         : LREAL;
    lrLastX     : LREAL := 1.0E9;
    lrLastY     : LREAL;
    lrLastZ     : LREAL;
    // EtherCAT task timing monitor (GVL.EcProf*).
    tProfEnd    : LTIME;
    tProfPrevT0 : LTIME;
    xProfPrev   : BOOL;
    lrPeriodUs  : LREAL;
    lrDevUs     : LREAL;
    lrAgsmUs    : LREAL;
    lrRestUs    : LREAL;
    iProf       : INT;
    iStatTick   : INT;
    // Set-position glitch monitor (GVL.SetGlitch*).
    lrSetPrev   : ARRAY[0..2] OF LREAL;
    lrVelPrev   : ARRAY[0..2] OF LREAL;
    lrV         : LREAL;
    lrD2        : LREAL;
    iSetValid   : ARRAY[0..2] OF INT;
    pAxG        : POINTER TO AXIS_REF_SM3;
    // Cycle position and the drives' SM-event counters (GVL.EcTic*, Drv*).
    lrTicUs     : LREAL;
    uliTicRaw   : ULINT;
    iTicN       : INT;
    lrTicLo     : LREAL := 1.0E9;
    lrTicHi     : LREAL;
    fbSdo       : ETC_CO_SdoRead4;
    iSdoStep    : INT;
    iSdoWait    : INT;
    uiSdoDev    : UINT;
    bySdoSub    : BYTE;
    diSdoVal    : DINT;
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

// ---- EtherCAT task timing monitor (GVL.EcProf*) ----
// Last program of EtherCAT_Task: stamps the end of the task's IEC code.
tProfEnd := LTIME();
IF GVL.EcProfReset THEN
    GVL.EcProfReset := FALSE;
    GVL.EcProfCycles := 0;
    GVL.EcPeriodUsMin := 1.0E9;
    GVL.EcPeriodUsMax := 0;
    GVL.EcLate100 := 0;
    GVL.EcLate500 := 0;
    FOR iProf := 0 TO 5 DO
        GVL.EcPeriodHist[iProf] := 0;
    END_FOR;
    GVL.EcAgsmUsMax := 0;
    GVL.EcAgsmUsAvg := 0;
    GVL.EcRestUsMax := 0;
    GVL.EcRestUsAvg := 0;
    GVL.EcDcOutCycles := 0;
    FOR iProf := 0 TO 2 DO
        GVL.SetGlitchCount[iProf] := 0;
        GVL.SetD2Max[iProf] := 0;
    END_FOR;
    GVL.EcTicUsMax := 0;
    FOR iProf := 0 TO 9 DO
        GVL.EcTicHist[iProf] := 0;
    END_FOR;
    xProfPrev := FALSE;
END_IF;
lrAgsmUs := ULINT_TO_LREAL(LTIME_TO_ULINT(GVL.EcProfT1 - GVL.EcProfT0)) / 1000.0;
lrRestUs := ULINT_TO_LREAL(LTIME_TO_ULINT(tProfEnd - GVL.EcProfT1)) / 1000.0;
IF xProfPrev THEN
    lrPeriodUs := ULINT_TO_LREAL(LTIME_TO_ULINT(GVL.EcProfT0 - tProfPrevT0)) / 1000.0;
    lrDevUs := ABS(lrPeriodUs - GVL.EcNominalUs);
    GVL.EcProfCycles := GVL.EcProfCycles + 1;
    GVL.EcPeriodUsMin := MIN(GVL.EcPeriodUsMin, lrPeriodUs);
    GVL.EcPeriodUsMax := MAX(GVL.EcPeriodUsMax, lrPeriodUs);
    IF lrPeriodUs > GVL.EcNominalUs + 100.0 THEN
        GVL.EcLate100 := GVL.EcLate100 + 1;
    END_IF;
    IF lrPeriodUs > GVL.EcNominalUs + 500.0 THEN
        GVL.EcLate500 := GVL.EcLate500 + 1;
    END_IF;
    IF lrDevUs < 20.0 THEN
        iProf := 0;
    ELSIF lrDevUs < 50.0 THEN
        iProf := 1;
    ELSIF lrDevUs < 100.0 THEN
        iProf := 2;
    ELSIF lrDevUs < 200.0 THEN
        iProf := 3;
    ELSIF lrDevUs < 500.0 THEN
        iProf := 4;
    ELSE
        iProf := 5;
    END_IF;
    GVL.EcPeriodHist[iProf] := GVL.EcPeriodHist[iProf] + 1;
    GVL.EcAgsmUsMax := MAX(GVL.EcAgsmUsMax, lrAgsmUs);
    GVL.EcRestUsMax := MAX(GVL.EcRestUsMax, lrRestUs);
    GVL.EcAgsmUsAvg := GVL.EcAgsmUsAvg * 0.999 + lrAgsmUs * 0.001;
    GVL.EcRestUsAvg := GVL.EcRestUsAvg * 0.999 + lrRestUs * 0.001;
    IF NOT IoConfig_Globals.EtherCAT_Master_SoftMotion.xDistributedClockInSync THEN
        GVL.EcDcOutCycles := GVL.EcDcOutCycles + 1;
    END_IF;
END_IF;
tProfPrevT0 := GVL.EcProfT0;
xProfPrev := TRUE;

// ---- Set-position glitch monitor (GVL.SetGlitch*) ----
FOR iProf := 0 TO 2 DO
    CASE iProf OF
        0: pAxG := ADR(IoConfig_Globals.EAxis0);
        1: pAxG := ADR(IoConfig_Globals.EAxis1);
    ELSE
        pAxG := ADR(IoConfig_Globals.EAxis2);
    END_CASE;
    lrV := pAxG^.fSetPosition - lrSetPrev[iProf];
    lrD2 := lrV - lrVelPrev[iProf];
    IF pAxG^.nAxisState = SMC_AXIS_STATE.power_off OR pAxG^.nAxisState = SMC_AXIS_STATE.errorstop THEN
        iSetValid[iProf] := 0;             // SetPosition / power-up: start over
    ELSIF iSetValid[iProf] < 2 THEN
        iSetValid[iProf] := iSetValid[iProf] + 1;
    ELSE
        GVL.SetD2Max[iProf] := MAX(GVL.SetD2Max[iProf], ABS(lrD2));
        IF ABS(lrD2) > GVL.SetGlitchThresh THEN
            GVL.SetGlitchCount[iProf] := GVL.SetGlitchCount[iProf] + 1;
            GVL.SetGlitchLastMs[iProf] := udiNow;
        END_IF;
    END_IF;
    lrSetPrev[iProf] := pAxG^.fSetPosition;
    lrVelPrev[iProf] := lrV;
END_FOR;

// ---- EtherCAT master frame statistics (GVL.EcLostFrames etc.) ----
IF GVL.EcStatsClear THEN
    GVL.EcStatsClear := FALSE;
    IoConfig_Globals.EtherCAT_Master_SoftMotion.ClearStatistics();
    iStatTick := 100;
END_IF;
iStatTick := iStatTick + 1;
IF iStatTick >= 100 THEN
    iStatTick := 0;
    IoConfig_Globals.EtherCAT_Master_SoftMotion.GetStatistics(
        udiLostFrameCount => GVL.EcLostFrames,
        udiTxErrorCount => GVL.EcTxErrors,
        udiRxErrorCount => GVL.EcRxErrors);
END_IF;

// ---- Cycle position at the end of the task's IEC code (GVL.EcTic*) ----
// Last thing in EtherCAT_Task; the frame is sent after it.
IoConfig_Globals.EtherCAT_Master_SoftMotion.GetCurrentTimeInCycle(pusTime := uliTicRaw);
lrTicUs := ULINT_TO_LREAL(uliTicRaw) / 1000.0;
GVL.EcTicUsMax := MAX(GVL.EcTicUsMax, lrTicUs);
lrTicLo := MIN(lrTicLo, lrTicUs);
lrTicHi := MAX(lrTicHi, lrTicUs);
iTicN := iTicN + 1;
IF iTicN >= 500 THEN
    GVL.EcTicTrMin[GVL.EcTicIdx] := LREAL_TO_REAL(lrTicLo);
    GVL.EcTicTrMax[GVL.EcTicIdx] := LREAL_TO_REAL(lrTicHi);
    GVL.EcTicIdx := (GVL.EcTicIdx + 1) MOD 20;
    iTicN := 0;
    lrTicLo := 1.0E9;
    lrTicHi := 0;
END_IF;
GVL.EcTicHist[LIMIT(0, LREAL_TO_INT(lrTicUs / 100.0 - 0.5), 9)] := GVL.EcTicHist[LIMIT(0, LREAL_TO_INT(lrTicUs / 100.0 - 0.5), 9)] + 1;

// ---- Drives' 0x1C32:0B / :20 by SDO, one after the other ----
// steps 0..5: drive (step / 2), subindex 0x0B (even) / 0x20 (odd); a
// pause of 200 cycles between reads.
IF iSdoWait > 0 THEN
    iSdoWait := iSdoWait - 1;
ELSE
    CASE iSdoStep / 2 OF
        0: uiSdoDev := 1002;
        1: uiSdoDev := 1003;
    ELSE
        uiSdoDev := 1005;
    END_CASE;
    bySdoSub := SEL((iSdoStep MOD 2) = 0, 16#20, 16#0B);
    fbSdo(xExecute := TRUE, usiCom := 1, uiDevice := uiSdoDev, wIndex := 16#1C32,
          bySubindex := bySdoSub, udiTimeOut := 500);
    IF fbSdo.xDone OR fbSdo.xError THEN
        IF fbSdo.xDone THEN
            diSdoVal := BYTE_TO_DINT(fbSdo.abyData[1]) + BYTE_TO_DINT(fbSdo.abyData[2]) * 256;
        ELSE
            diSdoVal := -1;
            GVL.EcSdoErr := TO_DINT(fbSdo.eError);
        END_IF;
        IF (iSdoStep MOD 2) = 0 THEN
            GVL.DrvSmMissed[iSdoStep / 2] := diSdoVal;
        ELSE
            GVL.DrvSyncErr[iSdoStep / 2] := diSdoVal;
        END_IF;
        fbSdo(xExecute := FALSE);
        iSdoStep := (iSdoStep + 1) MOD 6;
        iSdoWait := 200;
    END_IF;
END_IF;
