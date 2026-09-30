FUNCTION_BLOCK FB_Homing
VAR_INPUT
    bExecute : BOOL; // Comes from the Homing step being active
    udiTimeout : UDINT := 30000; // Phase-A (find-switch) timeout in ms.
    udiZeroTimeout : UDINT := 10000; // Phase-B (return-to-0) timeout in ms.
END_VAR
VAR_OUTPUT
    bDone : BOOL;     // Sets GVL_Robot.bHomingComplete
    bError : BOOL;    // Sets GVL_Robot.bError
    bBusy : BOOL;     // TRUE while the block is working
END_VAR
VAR
    // Home switches: a slot light barrier per joint, on when the arm is
    // raised into it; IO slave input N for EAxisN (terminal CH1). Positive
    // joint direction = arm up (both measured on the machine 2026-09-30).
    //
    // Phase A: every joint moves up at HOME_SEEK_VEL until its switch turns
    // on; the actual position on the scan it does (hit) is kept and the
    // joint stops. A joint whose switch is already on first backs off
    // HOME_BACKOFF down. The position is then shifted (MC_SetPosition,
    // relative) so that the hit reads GVL.HomeSwitchAngle[k] -- the switch
    // angle above level measured the same way (rising edge, up, 5 deg/s),
    // so the joints' 0 is level.
    // Phase B: all three move to 0 (level) together.
    //
    // Replaced SMC_Homing (FAST_BSLOW_S_STOP): it references on the switch
    // turning off while backing off slowly, a different point (the
    // sensor's hysteresis) from the one the angles were measured at.
    fbSeek    : ARRAY[0..2] OF SM3_Basic.MC_MoveRelative;
    fbHalt    : ARRAY[0..2] OF SM3_Basic.MC_Halt;
    fbSetPos  : ARRAY[0..2] OF SM3_Basic.MC_SetPosition;
    fbZero    : ARRAY[0..2] OF SM3_Basic.MC_MoveAbsolute;
    pAx       : ARRAY[0..2] OF POINTER TO AXIS_REF_SM3;
    bSeek     : ARRAY[0..2] OF BOOL;
    bHalt     : ARRAY[0..2] OF BOOL;
    bHit      : ARRAY[0..2] OF BOOL;
    fHitPos   : ARRAY[0..2] OF LREAL;
    bOn       : ARRAY[0..2] OF BOOL;
    bOnPrev   : ARRAY[0..2] OF BOOL;
    fSeekDist : ARRAY[0..2] OF LREAL;
    bSetPos   : BOOL;
    bExecZero : BOOL;
    k         : INT;
    bAll      : BOOL;
    bAny      : BOOL;

    iHomingState : INT := 0;
    fbTimeoutTimer : TON;
END_VAR
VAR CONSTANT
    HOME_SEEK_VEL  : LREAL := 5.0;     // deg/s up to the switch (as measured)
    HOME_SEEK_MAX  : LREAL := 60.0;    // deg: no switch within this = error
    HOME_BACKOFF   : LREAL := 8.0;     // deg down first if already on the switch
    ZeroVel        : LREAL := 10.0;
    ZeroAcc        : LREAL := 50.0;
    ZeroJerk       : LREAL := 500.0;
END_VAR
(* =========== IMPLEMENTATION =========== *)
pAx[0] := ADR(IoConfig_Globals.EAxis0);
pAx[1] := ADR(IoConfig_Globals.EAxis1);
pAx[2] := ADR(IoConfig_Globals.EAxis2);

// The switches: real input, or the simulated one for a virtual joint
// (GVL.SimHomeSwitchEnable: on above GVL.SimHomeSwitchPos).
FOR k := 0 TO 2 DO
    IF GVL.SimHomeSwitchEnable AND pAx[k]^.bVirtual THEN
        bOn[k] := pAx[k]^.fActPosition >= GVL.SimHomeSwitchPos;
    ELSE
        bOn[k] := (SHR(AxisGroupSM.InputCHs[1], k) AND 1) = 1;
    END_IF;
END_FOR;

// Abort: the caller dropped bExecute mid-sequence (Homing state left on
// timeout, EV_ERROR, EV_RESET).
IF NOT bExecute AND (iHomingState >= 10) AND (iHomingState <= 40) THEN
    FOR k := 0 TO 2 DO
        bSeek[k] := FALSE;
        bHalt[k] := FALSE;
    END_FOR;
    bSetPos := FALSE;
    bExecZero := FALSE;
    bBusy := FALSE;
    fbTimeoutTimer(IN := FALSE);
    iHomingState := 0;
END_IF

CASE iHomingState OF
    0: // Idle
        bBusy := FALSE;
        bDone := FALSE;
        bError := FALSE;
        bSetPos := FALSE;
        bExecZero := FALSE;
        FOR k := 0 TO 2 DO
            bSeek[k] := FALSE;
            bHalt[k] := FALSE;
        END_FOR;
        IF bExecute THEN
            iHomingState := 10;
        END_IF

    10: // Start: a joint already on its switch backs off first.
        bBusy := TRUE;
        fbTimeoutTimer(IN := FALSE);
        fbTimeoutTimer(IN := TRUE, PT := UDINT_TO_TIME(udiTimeout));
        FOR k := 0 TO 2 DO
            bHit[k] := FALSE;
            fHitPos[k] := 0;
            fSeekDist[k] := -HOME_BACKOFF;
            bSeek[k] := bOn[k];
        END_FOR;
        iHomingState := 15;

    15: // Back-offs done (none needed: at once).
        fbTimeoutTimer(IN := TRUE, PT := UDINT_TO_TIME(udiTimeout));
        bAll := TRUE;
        FOR k := 0 TO 2 DO
            IF bSeek[k] AND NOT fbSeek[k].Done THEN
                bAll := FALSE;
            END_IF;
        END_FOR;
        IF bAll THEN
            FOR k := 0 TO 2 DO
                bSeek[k] := FALSE;          // falling edge before the seek
            END_FOR;
            iHomingState := 17;
        ELSIF fbTimeoutTimer.Q THEN
            iHomingState := 99;
        END_IF;

    17: // All three seek up together.
        fbTimeoutTimer(IN := TRUE, PT := UDINT_TO_TIME(udiTimeout));
        FOR k := 0 TO 2 DO
            IF bOn[k] THEN                  // still on after backing off
                iHomingState := 99;
            END_IF;
            fSeekDist[k] := HOME_SEEK_MAX;
            bSeek[k] := TRUE;
        END_FOR;
        IF iHomingState = 17 THEN
            iHomingState := 20;
        END_IF;

    20: // Seek: stop each joint where its switch turns on.
        fbTimeoutTimer(IN := TRUE, PT := UDINT_TO_TIME(udiTimeout));
        bAll := TRUE;
        bAny := FALSE;
        FOR k := 0 TO 2 DO
            IF NOT bHit[k] THEN
                IF bOn[k] AND NOT bOnPrev[k] THEN
                    bHit[k] := TRUE;
                    fHitPos[k] := pAx[k]^.fActPosition;
                    bSeek[k] := FALSE;
                    bHalt[k] := TRUE;
                ELSIF fbSeek[k].Done THEN
                    bAny := TRUE;                  // full travel, no switch
                END_IF;
            END_IF;
            bAll := bAll AND bHit[k] AND (NOT bHalt[k] OR fbHalt[k].Done);
        END_FOR;
        IF bAll THEN
            FOR k := 0 TO 2 DO
                bHalt[k] := FALSE;
            END_FOR;
            fbTimeoutTimer(IN := FALSE);
            iHomingState := 25;
        ELSIF bAny OR fbTimeoutTimer.Q THEN
            iHomingState := 99;
        END_IF;

    25: // The hit becomes the switch angle (relative shift, all three).
        bSetPos := TRUE;
        IF fbSetPos[0].Done AND fbSetPos[1].Done AND fbSetPos[2].Done THEN
            bSetPos := FALSE;
            iHomingState := 30;
        END_IF;

    30: // Phase B start: all three to 0 (level) together.
        fbTimeoutTimer(IN := FALSE);
        bExecZero := TRUE;
        fbTimeoutTimer(IN := TRUE, PT := UDINT_TO_TIME(udiZeroTimeout));
        iHomingState := 40;

    40: // Phase B wait.
        fbTimeoutTimer(IN := TRUE, PT := UDINT_TO_TIME(udiZeroTimeout));
        IF fbZero[0].Done AND fbZero[1].Done AND fbZero[2].Done THEN
            iHomingState := 50;
        ELSIF fbZero[0].Error OR fbZero[1].Error OR fbZero[2].Error OR fbTimeoutTimer.Q THEN
            iHomingState := 99;
        END_IF

    50: // Done.
        bBusy := FALSE;
        bDone := TRUE;
        bExecZero := FALSE;
        fbTimeoutTimer(IN := FALSE);
        IF NOT bExecute THEN
            iHomingState := 0;
        END_IF

    99: // Error: no switch, a move failed, or a timeout.
        bBusy := FALSE;
        bError := TRUE;
        bSetPos := FALSE;
        bExecZero := FALSE;
        FOR k := 0 TO 2 DO
            bSeek[k] := FALSE;
            bHalt[k] := FALSE;
        END_FOR;
        fbTimeoutTimer(IN := FALSE);
        IF NOT bExecute THEN
            iHomingState := 0;
        END_IF
END_CASE;

FOR k := 0 TO 2 DO
    fbSeek[k](Axis := pAx[k]^, Execute := bSeek[k], Distance := fSeekDist[k],
              Velocity := HOME_SEEK_VEL, Acceleration := HOME_SEEK_VEL * 5,
              Deceleration := HOME_SEEK_VEL * 5, Jerk := HOME_SEEK_VEL * 50);
    fbHalt[k](Axis := pAx[k]^, Execute := bHalt[k],
              Deceleration := HOME_SEEK_VEL * 20, Jerk := HOME_SEEK_VEL * 400);
    fbSetPos[k](Axis := pAx[k]^, Execute := bSetPos, Position := GVL.HomeSwitchAngle[k] - fHitPos[k],
                Mode := TRUE);   // relative: the hit reads HomeSwitchAngle[k]
    fbZero[k](Axis := pAx[k]^, Execute := bExecZero, Position := 0.0, Velocity := ZeroVel,
              Acceleration := ZeroAcc, Deceleration := ZeroAcc, Jerk := ZeroJerk,
              Direction := SM3_Basic.MC_DIRECTION.positive);
    IF (iHomingState >= 10) AND (iHomingState <= 40)
       AND (fbSeek[k].Error OR fbHalt[k].Error OR fbSetPos[k].Error) THEN
        iHomingState := 99;
    END_IF;
    bOnPrev[k] := bOn[k];
END_FOR;

// GVL.HomeLastHitPos: where each switch turned on in the last homing (before
// the shift), for diagnostics.
GVL.HomeLastHitPos := fHitPos;
