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
    diSetPrev   : ARRAY[0..2] OF DINT;
    diVelPrev   : ARRAY[0..2] OF DINT;
    diVel         : DINT;
    diD2        : DINT;
    diTap       : DINT;
    diTapPrev   : ARRAY[0..2] OF DINT;
    diTapVelPrev : ARRAY[0..2] OF DINT;
    diTapVel    : DINT;
    diTapD2     : DINT;
    diDem       : DINT;
    diDemPrev   : ARRAY[0..2] OF DINT;
    diDemVelPrev : ARRAY[0..2] OF DINT;
    diDemVel    : DINT;
    diTHist     : ARRAY[0..2, 0..7] OF DINT;    // sent target, this cycle .. 7 cycles before
    aRingT      : ARRAY[0..2, 0..31] OF DINT;   // last 32 cycles of sent target / drive demand
    aRingD      : ARRAY[0..2, 0..31] OF DINT;
    iRing       : INT;
    iPost       : ARRAY[0..2] OF INT;           // cycles until a snapshot is taken
    iPostWhy    : ARRAY[0..2] OF INT;
    udiPostMs   : ARRAY[0..2] OF UDINT;
    iSnapJ      : INT;
    iLagN       : INT;
    xLate       : BOOL;
    xEarly      : BOOL;
    // Feedback shock monitor (GVL.Fb*).
    diFbPrev    : ARRAY[0..2] OF DINT;
    diFbVelPrev : ARRAY[0..2] OF DINT;
    iFbTqPrev   : ARRAY[0..2] OF INT;
    iFbValid    : ARRAY[0..2] OF INT;
    iFbK        : INT;
    iFbJ        : INT;
    diFbPos     : DINT;
    diFbVel     : DINT;
    diFbAbs     : DINT;
    iFbTq       : INT;
    iFbTqAbs    : INT;
    wFbSw       : WORD;
    // Settling time (GVL.Settle*).
    lrStSetPrev : ARRAY[0..2] OF LREAL;
    xStMoving   : BOOL;
    xStMovPrev  : BOOL;
    iStCap      : INT := -1;
    iStK        : INT;
    iStLast     : INT;
    arStX       : ARRAY[0..299] OF REAL;
    arStY       : ARRAY[0..299] OF REAL;
    arStZ       : ARRAY[0..299] OF REAL;
    lrStD       : LREAL;
    udiStAt     : UDINT;
    // Stop trace (GVL.Tr*): ring of the last 128 cycles, staged trace.
    fbStSet     : SM3_Robotics.SMC_GroupReadSetPosition;
    arRgC       : ARRAY[0..127, 0..2] OF REAL;
    arRgA       : ARRAY[0..127, 0..2] OF REAL;
    iRg         : INT;
    arTgC       : ARRAY[0..399, 0..2] OF REAL;
    arTgA       : ARRAY[0..399, 0..2] OF REAL;
    iTrK        : INT;
    iSyncMin    : INT := 32767;
    iSyncMax    : INT := -32768;
    udiSyncBucket : UDINT;
    uiArrMin    : UINT := 16#FFFF;
    uiArrMax    : UINT;
    udiArrBucket : UDINT;
    xLatePrev   : ARRAY[0..2] OF BOOL;
    xEarlyPrev  : ARRAY[0..2] OF BOOL;
    xGlitchD    : BOOL;
    iLag        : INT;
    iLagHit     : INT;
    lrFe        : LREAL;
    lrFePrev    : ARRAY[0..3] OF LREAL;
    iFeValid    : ARRAY[0..3] OF INT;
    iFeK        : INT;
    pAxFe       : POINTER TO AXIS_REF_SM3;
    lrFeJ       : LREAL;
    uiEspLatePrevMs : UINT;
    udiEspSecPrev   : UDINT;
    // Cycle position and the drives' SM-event counters (GVL.EcTic*, Drv*).
    lrTicUs     : LREAL;
    uliTicRaw   : ULINT;
    iTicN       : INT;
    lrTicLo     : LREAL := 1.0E9;
    lrTicHi     : LREAL;
    fbSdo       : ETC_CO_SdoRead4;
    fbSdoReqR   : ETC_CO_SdoRead4;      // GVL.SdoReq* (DRV_SDO)
    fbSdoReqW   : ETC_CO_SdoWrite4;
    abySdoW     : ARRAY[1..4] OF BYTE;
    aRingS      : ARRAY[0..2, 0..31] OF WORD;
    wStat       : WORD;
    xSyncOk     : BOOL;
    // Direct drive test (GVL.Dir*).
    diDirStart  : DINT;
    diDirTarget : DINT;
    iDirDir     : INT;
    wSw         : WORD;
    xDirAllowed : BOOL;
    iSdoStep    : INT;
    iSdoWait    : INT;
    uiSdoDev    : UINT;
    bySdoSub    : BYTE;
    diSdoVal    : DINT;
END_VAR
VAR CONSTANT
    // Drive objects read by SDO: 1C32:01 sync type, :02 cycle time ns,
    // :03 shift time ns, :04 supported sync types, :05 min cycle ns,
    // :06 calc+copy ns, :09 delay ns, :0B SM-event missed, :0C cycle too
    // small, :20 sync error; 60C2:01 interpolation period value, :02 its
    // index (10^x s).
    SDO_IDX : ARRAY[0..11] OF WORD := [10(16#1C32), 16#60C2, 16#60C2];
    SDO_SUB : ARRAY[0..11] OF BYTE := [16#01, 16#02, 16#03, 16#04, 16#05, 16#06, 16#09, 16#0B, 16#0C, 16#20, 16#01, 16#02];
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
        GVL.DiGlitchCount[iProf] := 0;
        GVL.DiD2Max[iProf] := 0;
        GVL.TapGlitch[iProf] := 0;
        GVL.TapD2Max[iProf] := 0;
        GVL.TapEqNow[iProf] := 0;
        GVL.TapEqPrev[iProf] := 0;
        GVL.TapEqNone[iProf] := 0;
        GVL.DemReset := TRUE;
    END_FOR;
    GVL.EcTicUsMax := 0;
    FOR iFeK := 0 TO 3 DO
        GVL.FeJumpMax[iFeK] := 0;
        GVL.FeJumpCount[iFeK] := 0;
    END_FOR;
    GVL.EspArrMaxPeak := 0;
    GVL.EspLateSum := 0;
    GVL.EspStale0 := GVL.EspStale;
    GVL.EspSkip0 := GVL.EspSkip;
    GVL.EspPosGlitch0 := GVL.EspPosGlitch;
    GVL.EspPosD2Peak := 0;
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
    diVel := pAxG^.diSetPosition - diSetPrev[iProf];   // wraps like the drive's 32-bit target
    diD2 := diVel - diVelPrev[iProf];
    lrD2 := lrV - lrVelPrev[iProf];
    IF (pAxG^.nAxisState = SMC_AXIS_STATE.power_off OR pAxG^.nAxisState = SMC_AXIS_STATE.errorstop)
       AND NOT (iProf = 0 AND GVL.DirRunning) THEN
        iSetValid[iProf] := 0;             // SetPosition / power-up: start over
    ELSIF iSetValid[iProf] < 2 THEN
        iSetValid[iProf] := iSetValid[iProf] + 1;
    ELSE
        GVL.SetD2Max[iProf] := MAX(GVL.SetD2Max[iProf], ABS(lrD2));
        GVL.DiD2Max[iProf] := MAX(GVL.DiD2Max[iProf], ABS(diD2));
        IF ABS(diD2) > GVL.DiGlitchThresh THEN
            GVL.DiGlitchCount[iProf] := GVL.DiGlitchCount[iProf] + 1;
        END_IF;
        IF ABS(lrD2) > GVL.SetGlitchThresh THEN
            GVL.SetGlitchCount[iProf] := GVL.SetGlitchCount[iProf] + 1;
            GVL.SetGlitchLastMs[iProf] := udiNow;
        END_IF;
    END_IF;
    lrSetPrev[iProf] := pAxG^.fSetPosition;
    // Tap: the Target Position actually in the output image.
    CASE iProf OF
        0: diTap := GVL.TapTarget0;
        1: diTap := GVL.TapTarget1;
    ELSE
        diTap := GVL.TapTarget2;
    END_CASE;
    diTapVel := diTap - diTapPrev[iProf];
    diTapD2 := diTapVel - diTapVelPrev[iProf];
    IF iSetValid[iProf] >= 2 THEN
        GVL.TapD2Max[iProf] := MAX(GVL.TapD2Max[iProf], ABS(diTapD2));
        IF ABS(diTapD2) > GVL.DiGlitchThresh THEN
            GVL.TapGlitch[iProf] := GVL.TapGlitch[iProf] + 1;
        END_IF;
        IF diTap = pAxG^.diSetPosition THEN
            GVL.TapEqNow[iProf] := GVL.TapEqNow[iProf] + 1;
        ELSIF diTap = diSetPrev[iProf] THEN
            GVL.TapEqPrev[iProf] := GVL.TapEqPrev[iProf] + 1;
        ELSE
            GVL.TapEqNone[iProf] := GVL.TapEqNone[iProf] + 1;
        END_IF;
    END_IF;
    // The drive's own demand (0x6062) against the targets we sent.
    CASE iProf OF
        0: diDem := GVL.DemandTap0;
        1: diDem := GVL.DemandTap1;
    ELSE
        diDem := GVL.DemandTap2;
    END_CASE;
    FOR iLag := 7 TO 1 BY -1 DO
        diTHist[iProf, iLag] := diTHist[iProf, iLag - 1];
    END_FOR;
    diTHist[iProf, 0] := diTap;
    aRingT[iProf, iRing] := diTap;
    aRingD[iProf, iRing] := diDem;
    CASE iProf OF
        0: wStat := GVL.StatusTap0;
        1: wStat := GVL.StatusTap1;
    ELSE
        wStat := GVL.StatusTap2;
    END_CASE;
    aRingS[iProf, iRing] := wStat;
    xSyncOk := (wStat AND 16#4000) <> 0;
    diDemVel := diDem - diDemPrev[iProf];
    iLagN := LIMIT(0, GVL.DemLagNominal, 5);
    IF iSetValid[iProf] >= 2 THEN
        iLagHit := 7;
        FOR iLag := 6 TO 0 BY -1 DO
            IF diDem = diTHist[iProf, iLag] THEN
                iLagHit := iLag;
            END_IF;
        END_FOR;
        GVL.DemLag[iProf, iLagHit] := GVL.DemLag[iProf, iLagHit] + 1;
        // Aligned to the drive's delay: the target it should be using moved,
        // the demand did not (stale), or the demand is one cycle further back.
        IF (diDemVel = 0) AND (ABS(diTHist[iProf, iLagN] - diTHist[iProf, iLagN + 1]) > 50) THEN
            GVL.DemStale[iProf] := GVL.DemStale[iProf] + 1;
            GVL.DemLastStaleMs[iProf] := udiNow;
        END_IF;
        xLate := (diDem = diTHist[iProf, iLagN + 1]) AND (diDem <> diTHist[iProf, iLagN]);
        IF xLate THEN
            GVL.DemLate[iProf] := GVL.DemLate[iProf] + 1;
        END_IF;
        xEarly := (iLagN > 0) AND (diDem = diTHist[iProf, iLagN - 1]) AND (diDem <> diTHist[iProf, iLagN]);
        IF (xLate AND NOT xLatePrev[iProf]) OR (xEarly AND NOT xEarlyPrev[iProf]) THEN
            GVL.DemEvt[iProf, GVL.DemEvtN[iProf] MOD 1024] := udiNow * 4 + SEL(xLate, 2, 1);
            GVL.DemEvtN[iProf] := GVL.DemEvtN[iProf] + 1;
        END_IF;
        xLatePrev[iProf] := xLate;
        xEarlyPrev[iProf] := xEarly;
        IF NOT xSyncOk THEN
            GVL.SyncOffCount[iProf] := GVL.SyncOffCount[iProf] + 1;
            GVL.SyncOffLastMs[iProf] := udiNow;
            IF xLate THEN
                GVL.SyncOffAndLate[iProf] := GVL.SyncOffAndLate[iProf] + 1;
            END_IF;
        END_IF;
        GVL.DemD2Max[iProf] := MAX(GVL.DemD2Max[iProf], ABS(diDemVel - diDemVelPrev[iProf]));
        xGlitchD := ABS(diDemVel - diDemVelPrev[iProf]) > GVL.DiGlitchThresh;
        IF xGlitchD THEN
            GVL.DemGlitch[iProf] := GVL.DemGlitch[iProf] + 1;
        END_IF;
        // Snapshot: arm on the event, take it 12 cycles later.
        IF (xLate OR xGlitchD) AND (iPost[iProf] = 0) AND (GVL.DemSnapN < 8) THEN
            iPost[iProf] := 12;
            iPostWhy[iProf] := SEL(xLate, 2, 1);
            udiPostMs[iProf] := udiNow;
        END_IF;
    END_IF;
    IF iPost[iProf] > 0 THEN
        iPost[iProf] := iPost[iProf] - 1;
        IF (iPost[iProf] = 0) AND (GVL.DemSnapN < 8) THEN
            FOR iSnapJ := 0 TO 31 DO
                GVL.DemSnapT[GVL.DemSnapN, iSnapJ] := aRingT[iProf, (iRing + 1 + iSnapJ) MOD 32];
                GVL.DemSnapD[GVL.DemSnapN, iSnapJ] := aRingD[iProf, (iRing + 1 + iSnapJ) MOD 32];
                GVL.DemSnapS[GVL.DemSnapN, iSnapJ] := aRingS[iProf, (iRing + 1 + iSnapJ) MOD 32];
            END_FOR;
            GVL.DemSnapAxis[GVL.DemSnapN] := iProf;
            GVL.DemSnapMs[GVL.DemSnapN] := udiPostMs[iProf];
            GVL.DemSnapWhy[GVL.DemSnapN] := iPostWhy[iProf];
            GVL.DemSnapN := GVL.DemSnapN + 1;
        END_IF;
    END_IF;
    diDemPrev[iProf] := diDem;
    diDemVelPrev[iProf] := diDemVel;
    diTapPrev[iProf] := diTap;
    diTapVelPrev[iProf] := diTapVel;
    lrVelPrev[iProf] := lrV;
    diSetPrev[iProf] := pAxG^.diSetPosition;
    diVelPrev[iProf] := diVel;
END_FOR;
iRing := (iRing + 1) MOD 32;
IF GVL.DemReset THEN
    GVL.DemReset := FALSE;
    GVL.DemSnapN := 0;
    FOR iProf := 0 TO 2 DO
        GVL.DemStale[iProf] := 0;
        GVL.DemGlitch[iProf] := 0;
        GVL.DemD2Max[iProf] := 0;
        GVL.DemLate[iProf] := 0;
        GVL.DemEvtN[iProf] := 0;
        GVL.OddLogN := 0;
        GVL.ArrLogN := 0;
        GVL.SyncLogN := 0;
        GVL.SyncOffCount[iProf] := 0;
        GVL.SyncOffAndLate[iProf] := 0;
        iPost[iProf] := 0;
        FOR iLag := 0 TO 7 DO
            GVL.DemLag[iProf, iLag] := 0;
        END_FOR;
    END_FOR;
END_IF;

// ---- Bus timing probe on the EasyCAT (GVL.Esp*) ----
GVL.EspSeq := GVL.EspSeq + 1;
ecat_esp_out8 := TO_USINT(GVL.EspSeq AND 16#FF);
ecat_esp_out9 := TO_USINT(SHR(GVL.EspSeq, 8) AND 16#FF);
ecat_esp_out10 := TO_USINT(SHR(GVL.EspSeq, 16) AND 16#FF);
ecat_esp_out11 := TO_USINT(SHR(GVL.EspSeq, 24) AND 16#FF);
GVL.EspStale := TO_UINT(ecat_esp_in22) + TO_UINT(ecat_esp_in23) * 256;
GVL.EspSkip := TO_UINT(ecat_esp_in24) + TO_UINT(ecat_esp_in25) * 256;
// Inputs 26-29: odd SYNC0 intervals (count, last deviation us) since the
// 2026-10-01 firmware (were the arrival min / max); logged per event
// (GVL.OddLog*).
// Inputs 26-27: this SYNC0 interval minus the cycle, ns (MCPWM capture,
// 2026-10-01 firmware); logged per 100 ms (GVL.SyncLog*).
GVL.EspSyncDevNs := UINT_TO_INT(TO_UINT(ecat_esp_in26) + TO_UINT(ecat_esp_in27) * 256);
IF GVL.EspSyncDevNs <> -32768 THEN
    iSyncMin := MIN(iSyncMin, GVL.EspSyncDevNs);
    iSyncMax := MAX(iSyncMax, GVL.EspSyncDevNs);
END_IF;
IF udiNow - udiSyncBucket >= 100 THEN
    IF iSyncMax >= iSyncMin THEN
        GVL.SyncLogMs[GVL.SyncLogN MOD 4096] := udiSyncBucket;
        GVL.SyncLogMin[GVL.SyncLogN MOD 4096] := iSyncMin;
        GVL.SyncLogMax[GVL.SyncLogN MOD 4096] := iSyncMax;
        GVL.SyncLogN := GVL.SyncLogN + 1;
    END_IF;
    udiSyncBucket := udiNow;
    iSyncMin := 32767;
    iSyncMax := -32768;
END_IF;
GVL.EspOddDev := 0;
// Inputs 28-29: frame arrival after SYNC0, us; logged per 100 ms (GVL.ArrLog*).
GVL.EspArrUs := TO_UINT(ecat_esp_in28) + TO_UINT(ecat_esp_in29) * 256;
IF GVL.EspArrUs <> 16#FFFF THEN
    uiArrMin := MIN(uiArrMin, GVL.EspArrUs);
    uiArrMax := MAX(uiArrMax, GVL.EspArrUs);
END_IF;
IF udiNow - udiArrBucket >= 100 THEN
    IF uiArrMax >= uiArrMin THEN
        GVL.ArrLogMs[GVL.ArrLogN MOD 4096] := udiArrBucket;
        GVL.ArrLogMin[GVL.ArrLogN MOD 4096] := uiArrMin;
        GVL.ArrLogMax[GVL.ArrLogN MOD 4096] := uiArrMax;
        GVL.ArrLogN := GVL.ArrLogN + 1;
    END_IF;
    udiArrBucket := udiNow;
    uiArrMin := 16#FFFF;
    uiArrMax := 0;
END_IF;
GVL.EspArrMinUs := 0;
GVL.EspArrMaxUs := 0;

GVL.EspLate := TO_UINT(ecat_esp_in30) + TO_UINT(ecat_esp_in31) * 256;
ecat_esp_out12 := TO_USINT(GVL.TapTarget0 AND 16#FF);
ecat_esp_out13 := TO_USINT(SHR(GVL.TapTarget0, 8) AND 16#FF);
ecat_esp_out14 := TO_USINT(SHR(GVL.TapTarget0, 16) AND 16#FF);
ecat_esp_out15 := TO_USINT(SHR(GVL.TapTarget0, 24) AND 16#FF);
GVL.EspPosGlitch := TO_UINT(ecat_esp_in11) + TO_UINT(ecat_esp_in12) * 256;
GVL.EspPosD2Max := (TO_UDINT(ecat_esp_in13) + TO_UDINT(ecat_esp_in14) * 256) * 16;
GVL.EspPosD2Peak := MAX(GVL.EspPosD2Peak, GVL.EspPosD2Max);
GVL.EspArrMaxPeak := MAX(GVL.EspArrMaxPeak, GVL.EspArrMaxUs);
// The ESP32 publishes a new min / max / late once a second; sum late per second.
IF udiNow - udiEspSecPrev >= 1000 THEN
    udiEspSecPrev := udiNow;
    GVL.EspLateSum := GVL.EspLateSum + GVL.EspLate;
END_IF;

// ---- Feedback check: following-error jumps (GVL.FeJump*) ----
FOR iFeK := 0 TO 3 DO
    CASE iFeK OF
        0: pAxFe := ADR(IoConfig_Globals.EAxis0);
        1: pAxFe := ADR(IoConfig_Globals.EAxis1);
        2: pAxFe := ADR(IoConfig_Globals.EAxis2);
    ELSE
        pAxFe := ADR(IoConfig_Globals.reelpullmotor);
    END_CASE;
    lrFe := pAxFe^.fSetPosition - pAxFe^.fActPosition;
    IF pAxFe^.bVirtual OR pAxFe^.nAxisState = SMC_AXIS_STATE.power_off
       OR pAxFe^.nAxisState = SMC_AXIS_STATE.errorstop THEN
        iFeValid[iFeK] := 0;
    ELSIF iFeValid[iFeK] < 2 THEN
        iFeValid[iFeK] := iFeValid[iFeK] + 1;
    ELSE
        // Modulo axes (the reel, 200 mm): set and actual wrap in different
        // cycles, so fe steps by one period there -- take it out.
        lrFeJ := lrFe - lrFePrev[iFeK];
        IF (pAxFe^.fPositionPeriod > 0) AND (lrFeJ > pAxFe^.fPositionPeriod / 2) THEN
            lrFeJ := lrFeJ - pAxFe^.fPositionPeriod;
        ELSIF (pAxFe^.fPositionPeriod > 0) AND (lrFeJ < -pAxFe^.fPositionPeriod / 2) THEN
            lrFeJ := lrFeJ + pAxFe^.fPositionPeriod;
        END_IF;
        GVL.FeJumpMax[iFeK] := MAX(GVL.FeJumpMax[iFeK], ABS(lrFeJ));
        IF ABS(lrFeJ) > GVL.FeJumpThresh[iFeK] THEN
            GVL.FeJumpCount[iFeK] := GVL.FeJumpCount[iFeK] + 1;
            GVL.FeLastJumpMs[iFeK] := udiNow;
        END_IF;
    END_IF;
    lrFePrev[iFeK] := lrFe;
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

// ---- The delta drives' sync objects by SDO (GVL.DrvObj*) ----
// Step k: drive k / 12 (EAxis0/1/2 = station 1004 / 1005 / 1006; wire and
// tree order since 2026-10-02: EC0808DN 1001, QEC 1002, reel 1003, EAxis0..2,
// EasyCAT 1007), object k MOD 12 of
// SDO_IDX / SDO_SUB; a pause of 50 cycles between reads, ~2 s a sweep.
// A DRV_SDO request (GVL.SdoReq*) runs first; the sweep waits.
IF GVL.SdoReqActive THEN
    IF GVL.SdoReqWrite THEN
        abySdoW[1] := DINT_TO_BYTE(GVL.SdoReqValue AND 16#FF);
        abySdoW[2] := DINT_TO_BYTE(SHR(GVL.SdoReqValue, 8) AND 16#FF);
        abySdoW[3] := DINT_TO_BYTE(SHR(GVL.SdoReqValue, 16) AND 16#FF);
        abySdoW[4] := DINT_TO_BYTE(SHR(GVL.SdoReqValue, 24) AND 16#FF);
        fbSdoReqW(xExecute := TRUE, usiCom := 1, uiDevice := GVL.SdoReqStation, wIndex := GVL.SdoReqIndex,
                  bySubindex := GVL.SdoReqSub, udiTimeOut := 1000, usiDataLength := GVL.SdoReqSize,
                  abyData := abySdoW);
        IF fbSdoReqW.xDone OR fbSdoReqW.xError THEN
            GVL.SdoResOk := fbSdoReqW.xDone;
            GVL.SdoResErr := SEL(fbSdoReqW.xError, 0, TO_DINT(fbSdoReqW.eError));
            GVL.SdoResValue := GVL.SdoReqValue;
            fbSdoReqW(xExecute := FALSE);
            GVL.SdoResSeq := GVL.SdoReqSeq;
            GVL.SdoReqActive := FALSE;
        END_IF;
    ELSE
        fbSdoReqR(xExecute := TRUE, usiCom := 1, uiDevice := GVL.SdoReqStation, wIndex := GVL.SdoReqIndex,
                  bySubindex := GVL.SdoReqSub, udiTimeOut := 1000);
        IF fbSdoReqR.xDone OR fbSdoReqR.xError THEN
            GVL.SdoResOk := fbSdoReqR.xDone;
            GVL.SdoResErr := SEL(fbSdoReqR.xError, 0, TO_DINT(fbSdoReqR.eError));
            GVL.SdoResValue := BYTE_TO_DINT(fbSdoReqR.abyData[1]) + SHL(BYTE_TO_DINT(fbSdoReqR.abyData[2]), 8)
                               + SHL(BYTE_TO_DINT(fbSdoReqR.abyData[3]), 16) + SHL(BYTE_TO_DINT(fbSdoReqR.abyData[4]), 24);
            fbSdoReqR(xExecute := FALSE);
            GVL.SdoResSeq := GVL.SdoReqSeq;
            GVL.SdoReqActive := FALSE;
        END_IF;
    END_IF;
ELSIF NOT GVL.SdoSweepEnable THEN
    ;   // background reads off (DRV_SDO requests still run)
ELSIF iSdoWait > 0 THEN
    iSdoWait := iSdoWait - 1;
ELSE
    CASE iSdoStep / 12 OF
        0: uiSdoDev := 1004;
        1: uiSdoDev := 1005;
    ELSE
        uiSdoDev := 1006;
    END_CASE;
    fbSdo(xExecute := TRUE, usiCom := 1, uiDevice := uiSdoDev, wIndex := SDO_IDX[iSdoStep MOD 12],
          bySubindex := SDO_SUB[iSdoStep MOD 12], udiTimeOut := 500);
    IF fbSdo.xDone OR fbSdo.xError THEN
        IF fbSdo.xDone THEN
            diSdoVal := BYTE_TO_DINT(fbSdo.abyData[1]) + SHL(BYTE_TO_DINT(fbSdo.abyData[2]), 8)
                        + SHL(BYTE_TO_DINT(fbSdo.abyData[3]), 16) + SHL(BYTE_TO_DINT(fbSdo.abyData[4]), 24);
            GVL.DrvObjErr[iSdoStep / 12, iSdoStep MOD 12] := 0;
        ELSE
            diSdoVal := -1;
            GVL.DrvObjErr[iSdoStep / 12, iSdoStep MOD 12] := TO_DINT(fbSdo.eError);
            GVL.EcSdoErr := TO_DINT(fbSdo.eError);
        END_IF;
        GVL.DrvObj[iSdoStep / 12, iSdoStep MOD 12] := diSdoVal;
        fbSdo(xExecute := FALSE);
        iSdoStep := (iSdoStep + 1) MOD 36;
        iSdoWait := 50;
    END_IF;
END_IF;
GVL.DrvSmMissed[0] := GVL.DrvObj[0, 7];
GVL.DrvSmMissed[1] := GVL.DrvObj[1, 7];
GVL.DrvSmMissed[2] := GVL.DrvObj[2, 7];
GVL.DrvSyncErr[0] := GVL.DrvObj[0, 9];
GVL.DrvSyncErr[1] := GVL.DrvObj[1, 9];
GVL.DrvSyncErr[2] := GVL.DrvObj[2, 9];

// ---- Direct drive test without SoftMotion (GVL.Dir*, SYS DIRECT) ----
// Runs only while EAxis0 is virtual in SoftMotion (SoftMotion leaves the
// drive alone) and the FSM is UnInited. Idle (DirState 0) it does not touch
// the outputs.
xDirAllowed := IoConfig_Globals.EAxis0.bVirtual
               AND (AxisGroupSM.AxisGroupManagerFb._eState = E_RobotState.UnInited);
wSw := GVL.StatusTap0 AND 16#006F;
IF GVL.DirEnable AND (GVL.DirState <> 99) THEN
    IF NOT xDirAllowed THEN
        GVL.DirReason := 'not_allowed';
        GVL.DirState := 99;
    ELSIF udiNow - GVL.DirHbMs > 2000 THEN
        GVL.DirReason := 'heartbeat';
        GVL.DirState := 99;
    ELSIF (GVL.StatusTap0 AND 16#0008) <> 0 AND GVL.DirState >= 3 THEN
        GVL.DirReason := 'drive_fault';
        GVL.DirState := 99;
    END_IF;
END_IF;
IF GVL.DirEnable THEN
    CASE GVL.DirState OF
        0:  // start: target = actual, fault reset if needed
            GVL.TapTarget0 := GVL.ActualTap0;
            IF (GVL.StatusTap0 AND 16#0008) <> 0 THEN
                GVL.CtlTap0 := 16#0080;
            ELSE
                GVL.CtlTap0 := 16#0006;
            END_IF;
            GVL.DirCycles := 0;
            GVL.DirReason := '';
            GVL.DirState := 1;
        1:  // shutdown -> ready to switch on
            GVL.TapTarget0 := GVL.ActualTap0;
            GVL.CtlTap0 := 16#0006;
            IF wSw = 16#0021 THEN
                GVL.DirState := 2;
            END_IF;
        2:  // switch on
            GVL.TapTarget0 := GVL.ActualTap0;
            GVL.CtlTap0 := 16#0007;
            IF wSw = 16#0023 THEN
                GVL.DirState := 3;
            END_IF;
        3:  // enable operation, holding the actual position
            GVL.TapTarget0 := GVL.ActualTap0;
            GVL.CtlTap0 := 16#000F;
            IF wSw = 16#0027 THEN
                diDirStart := GVL.ActualTap0;
                diDirTarget := diDirStart;
                iDirDir := 1;
                GVL.DirState := 4;
            END_IF;
        4:  // constant step up DirTravelInc, then back
            diDirTarget := diDirTarget + iDirDir * GVL.DirStepInc;
            IF (iDirDir > 0) AND (diDirTarget - diDirStart >= MIN(GVL.DirTravelInc, GVL.DirTravelMax)) THEN
                iDirDir := -1;
            ELSIF (iDirDir < 0) AND (diDirTarget <= diDirStart) THEN
                diDirTarget := diDirStart;
                GVL.DirState := 5;
            END_IF;
            IF ABS(diDirTarget - GVL.ActualTap0) > GVL.DirMaxFe THEN
                GVL.DirReason := 'following_error';
                GVL.DirState := 99;
            ELSE
                GVL.TapTarget0 := diDirTarget;
                GVL.CtlTap0 := 16#000F;
                GVL.DirCycles := GVL.DirCycles + 1;
            END_IF;
        5:  // done: hold
            GVL.TapTarget0 := diDirTarget;
            GVL.CtlTap0 := 16#000F;
    ELSE    // 99 aborted: disable the drive, hold
        GVL.TapTarget0 := GVL.ActualTap0;
        GVL.CtlTap0 := 16#0006;
    END_CASE;
ELSIF GVL.DirState <> 0 THEN
    // stop: disable the drive once, then leave the outputs alone
    GVL.TapTarget0 := GVL.ActualTap0;
    GVL.CtlTap0 := 16#0006;
    GVL.DirState := 0;
END_IF;
GVL.DirRunning := GVL.DirEnable AND ((GVL.DirState = 4) OR (GVL.DirState = 5));

// ---- Feedback shock monitor (GVL.Fb*, SYS FB_STATS) ----
// Per drive in Operation enabled: |d2 of the actual position| and |torque
// change| per cycle into histograms, to compare command filters.
FOR iFbK := 0 TO 2 DO
    CASE iFbK OF
        0: diFbPos := GVL.ActualTap0; iFbTq := GVL.TorqueTap0; wFbSw := GVL.StatusTap0;
        1: diFbPos := GVL.ActualTap1; iFbTq := GVL.TorqueTap1; wFbSw := GVL.StatusTap1;
    ELSE
        diFbPos := GVL.ActualTap2; iFbTq := GVL.TorqueTap2; wFbSw := GVL.StatusTap2;
    END_CASE;
    diFbVel := diFbPos - diFbPrev[iFbK];
    IF (wFbSw AND 16#006F) = 16#0027 THEN
        IF iFbValid[iFbK] < 2 THEN
            iFbValid[iFbK] := iFbValid[iFbK] + 1;
        ELSE
            diFbAbs := ABS(diFbVel - diFbVelPrev[iFbK]);
            IF diFbAbs < 1000 THEN iFbJ := 0;
            ELSIF diFbAbs < 2000 THEN iFbJ := 1;
            ELSIF diFbAbs < 5000 THEN iFbJ := 2;
            ELSIF diFbAbs < 10000 THEN iFbJ := 3;
            ELSIF diFbAbs < 20000 THEN iFbJ := 4;
            ELSIF diFbAbs < 50000 THEN iFbJ := 5;
            ELSIF diFbAbs < 100000 THEN iFbJ := 6;
            ELSE iFbJ := 7;
            END_IF;
            GVL.FbD2Hist[iFbK, iFbJ] := GVL.FbD2Hist[iFbK, iFbJ] + 1;
            GVL.FbD2Max[iFbK] := MAX(GVL.FbD2Max[iFbK], diFbAbs);
            iFbTqAbs := ABS(iFbTq - iFbTqPrev[iFbK]);
            IF iFbTqAbs < 5 THEN iFbJ := 0;
            ELSIF iFbTqAbs < 10 THEN iFbJ := 1;
            ELSIF iFbTqAbs < 20 THEN iFbJ := 2;
            ELSIF iFbTqAbs < 50 THEN iFbJ := 3;
            ELSIF iFbTqAbs < 100 THEN iFbJ := 4;
            ELSIF iFbTqAbs < 200 THEN iFbJ := 5;
            ELSIF iFbTqAbs < 500 THEN iFbJ := 6;
            ELSE iFbJ := 7;
            END_IF;
            GVL.FbTqHist[iFbK, iFbJ] := GVL.FbTqHist[iFbK, iFbJ] + 1;
            GVL.FbTqMax[iFbK] := MAX(GVL.FbTqMax[iFbK], iFbTqAbs);
        END_IF;
    ELSE
        iFbValid[iFbK] := 0;
    END_IF;
    diFbVelPrev[iFbK] := diFbVel;
    diFbPrev[iFbK] := diFbPos;
    iFbTqPrev[iFbK] := iFbTq;
END_FOR;
IF GVL.FbReset THEN
    GVL.FbReset := FALSE;
    FOR iFbK := 0 TO 2 DO
        FOR iFbJ := 0 TO 7 DO
            GVL.FbD2Hist[iFbK, iFbJ] := 0;
            GVL.FbTqHist[iFbK, iFbJ] := 0;
        END_FOR;
        GVL.FbD2Max[iFbK] := 0;
        GVL.FbTqMax[iFbK] := 0;
    END_FOR;
END_IF;

// ---- Settling time (GVL.Settle*, SYS SETTLE) ----
xStMoving := ABS(IoConfig_Globals.EAxis0.fSetPosition - lrStSetPrev[0]) > 1.0E-6
             OR ABS(IoConfig_Globals.EAxis1.fSetPosition - lrStSetPrev[1]) > 1.0E-6
             OR ABS(IoConfig_Globals.EAxis2.fSetPosition - lrStSetPrev[2]) > 1.0E-6;
lrStSetPrev[0] := IoConfig_Globals.EAxis0.fSetPosition;
lrStSetPrev[1] := IoConfig_Globals.EAxis1.fSetPosition;
lrStSetPrev[2] := IoConfig_Globals.EAxis2.fSetPosition;
// Stop trace: commanded and actual TCP every cycle into a 128-cycle ring.
fbStSet(AxisGroup := AxisGroupSM.AxisGroupManagerFb._agAxisGroup, Enable := TRUE,
        CoordSystem := SMC_COORD_SYSTEM.WCS);
arRgC[iRg, 0] := LREAL_TO_REAL(fbStSet.Position.c.X);
arRgC[iRg, 1] := LREAL_TO_REAL(fbStSet.Position.c.Y);
arRgC[iRg, 2] := LREAL_TO_REAL(fbStSet.Position.c.Z);
arRgA[iRg, 0] := LREAL_TO_REAL(AxisGroupSM.GroupActualPositionFb.Position.c.X);
arRgA[iRg, 1] := LREAL_TO_REAL(AxisGroupSM.GroupActualPositionFb.Position.c.Y);
arRgA[iRg, 2] := LREAL_TO_REAL(AxisGroupSM.GroupActualPositionFb.Position.c.Z);
IF xStMovPrev AND NOT xStMoving AND (iStCap < 0) AND AxisGroupSM.ArmPositionValid THEN
    iStCap := 0;
    udiStAt := udiNow;
    // the 100 cycles before the stop (this cycle is sample 100)
    FOR iTrK := 0 TO 99 DO
        arTgC[iTrK, 0] := arRgC[(iRg + 28 + iTrK) MOD 128, 0];
        arTgC[iTrK, 1] := arRgC[(iRg + 28 + iTrK) MOD 128, 1];
        arTgC[iTrK, 2] := arRgC[(iRg + 28 + iTrK) MOD 128, 2];
        arTgA[iTrK, 0] := arRgA[(iRg + 28 + iTrK) MOD 128, 0];
        arTgA[iTrK, 1] := arRgA[(iRg + 28 + iTrK) MOD 128, 1];
        arTgA[iTrK, 2] := arRgA[(iRg + 28 + iTrK) MOD 128, 2];
    END_FOR;
END_IF;
xStMovPrev := xStMoving;
IF iStCap >= 0 THEN
    IF xStMoving OR NOT AxisGroupSM.ArmPositionValid THEN
        iStCap := -1;                       // moving again before 300 cycles: not counted
    ELSE
        arStX[iStCap] := LREAL_TO_REAL(AxisGroupSM.GroupActualPositionFb.Position.c.X);
        arStY[iStCap] := LREAL_TO_REAL(AxisGroupSM.GroupActualPositionFb.Position.c.Y);
        arStZ[iStCap] := LREAL_TO_REAL(AxisGroupSM.GroupActualPositionFb.Position.c.Z);
        arTgC[100 + iStCap, 0] := arRgC[iRg, 0];
        arTgC[100 + iStCap, 1] := arRgC[iRg, 1];
        arTgC[100 + iStCap, 2] := arRgC[iRg, 2];
        arTgA[100 + iStCap, 0] := arStX[iStCap];
        arTgA[100 + iStCap, 1] := arStY[iStCap];
        arTgA[100 + iStCap, 2] := arStZ[iStCap];
        iStCap := iStCap + 1;
        IF iStCap >= 300 THEN
            iStLast := 0;
            FOR iStK := 0 TO 299 DO
                lrStD := SQRT(EXPT(REAL_TO_LREAL(arStX[iStK] - arStX[299]), 2)
                              + EXPT(REAL_TO_LREAL(arStY[iStK] - arStY[299]), 2)
                              + EXPT(REAL_TO_LREAL(arStZ[iStK] - arStZ[299]), 2));
                IF lrStD > GVL.SettleTolMm THEN
                    iStLast := iStK + 1;
                END_IF;
            END_FOR;
            lrStD := SQRT(EXPT(REAL_TO_LREAL(arStX[0] - arStX[299]), 2)
                          + EXPT(REAL_TO_LREAL(arStY[0] - arStY[299]), 2)
                          + EXPT(REAL_TO_LREAL(arStZ[0] - arStZ[299]), 2));
            GVL.SettleMs[GVL.SettleN MOD 256] := INT_TO_UDINT(iStLast) * DINT_TO_UDINT(AxisGroupSM.BusPeriod);
            GVL.SettleErrUm[GVL.SettleN MOD 256] := LREAL_TO_UDINT(lrStD * 1000.0);
            GVL.SettleAtMs[GVL.SettleN MOD 256] := udiStAt;
            GVL.SettleN := GVL.SettleN + 1;
            FOR iTrK := 0 TO 399 DO
                GVL.TrCX[iTrK] := arTgC[iTrK, 0];
                GVL.TrCY[iTrK] := arTgC[iTrK, 1];
                GVL.TrCZ[iTrK] := arTgC[iTrK, 2];
                GVL.TrAX[iTrK] := arTgA[iTrK, 0];
                GVL.TrAY[iTrK] := arTgA[iTrK, 1];
                GVL.TrAZ[iTrK] := arTgA[iTrK, 2];
            END_FOR;
            GVL.TrAtMs := udiStAt;
            GVL.TrSeq := GVL.TrSeq + 1;
            iStCap := -1;
        END_IF;
    END_IF;
END_IF;
iRg := (iRg + 1) MOD 128;
