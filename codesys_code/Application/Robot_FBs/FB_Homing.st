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
    // Phase A: three SMC_Homing run in parallel; each independently
    // searches the limit switch and stops there (bReturnToZero=FALSE)
    // so the encoder is set to fHomePosition without further motion.
    // The outer state machine waits until ALL three report bDone before
    // moving on -- this enforces "都到位後" (all-on-position) before
    // anyone leaves the home switch.
    fbHome_Axis0 : SM3_Basic.SMC_Homing;
    fbHome_Axis1 : SM3_Basic.SMC_Homing;
    fbHome_Axis2 : SM3_Basic.SMC_Homing;

    // Phase B: synchronized return-to-zero. Started AT THE SAME SCAN on
    // all three axes (state 30 entry) so they move toward joint 0 in
    // lockstep. Using MC_MoveAbsolute per axis (vs MC_GroupMove*) keeps
    // each axis in its own command stream -- the kinematic group is
    // still in ErrorStop after SMC_Homing per the comment in
    // AxisGroupManager.Update.st:171-178, and the SMC_GroupReset that
    // clears it only runs once the Ready state is entered.
    fbZero_Axis0 : SM3_Basic.MC_MoveAbsolute;
    fbZero_Axis1 : SM3_Basic.MC_MoveAbsolute;
    fbZero_Axis2 : SM3_Basic.MC_MoveAbsolute;

    // State machine + per-phase timeout. udiTimeout governs phase A
    // (mechanical switch search is slow and may travel the full axis),
    // udiZeroTimeout governs phase B (deterministic absolute move from
    // homePose to 0, shorter budget).
    iHomingState : INT := 0;
    fbTimeoutTimer : TON;

    // Triggers gated by state so SMC_Homing and MC_MoveAbsolute never
    // both assert bExecute on the same axis at once (SoftMotion
    // single-motion-FB-per-axis rule).
    bExecHoming : BOOL;
    bExecZero   : BOOL;
END_VAR

// --- Local Constants for Homing Parameters ---
VAR CONSTANT
    Speed: REAL := 10;
    // Joint position the encoder is set to when the home switch is hit.
    // Phase B then moves all three to 0 -- so 20 is the "lift-off home
    // switch" offset, NOT a final resting position. The arm visibly
    // settles at joint 0 after Phase B.
    homePose: REAL := 20.0;
    // Phase-B velocity / accel: slower than the Speed*N home-search
    // because the move starts from a known stop and is a deterministic
    // short hop. Conservative defaults; tune if mechanical settling
    // overshoots.
    ZeroVel: REAL := 10.0;
    ZeroAcc: REAL := 50.0;
    ZeroJerk: REAL := 500.0;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Two-phase homing per 2026-06-24 requirement:
//   Phase A (state 10/20): all three Delta axes find limit switches in
//                          parallel. SMC_Homing.bReturnToZero := FALSE
//                          so each axis stops at the switch with
//                          encoder = homePose. Wait for ALL three
//                          bDone before moving on.
//   Phase B (state 30/40): all three axes simultaneously
//                          MC_MoveAbsolute -> joint 0. Wait for ALL
//                          three bDone before reporting overall bDone.
//
// Reel and A axis are not homed (per spec); they remain in whatever
// state EV_GROUP_ENABLE left them.

CASE iHomingState OF
    0: // Idle
        bBusy := FALSE;
        bDone := FALSE;
        bError := FALSE;
        bExecHoming := FALSE;
        bExecZero := FALSE;

        IF bExecute THEN
            iHomingState := 10;
        END_IF

    10: // Phase A start: kick off SMC_Homing on all three.
        bBusy := TRUE;
        bExecHoming := TRUE;
        fbTimeoutTimer(IN:=TRUE, PT:=UDINT_TO_TIME(udiTimeout));
        iHomingState := 20;

    20: // Phase A wait: all three reached home switch.
        fbTimeoutTimer(IN:=TRUE, PT:=UDINT_TO_TIME(udiTimeout));

        IF fbHome_Axis0.bDone AND fbHome_Axis1.bDone AND fbHome_Axis2.bDone THEN
            // Drop phase-A trigger so SoftMotion releases the axes
            // before phase-B MC_MoveAbsolute claims them. The single-
            // motion-FB-per-axis rule means bExecute on a new FB while
            // the previous one is still active is rejected with error.
            bExecHoming := FALSE;
            fbTimeoutTimer(IN:=FALSE);
            iHomingState := 30;

        ELSIF fbHome_Axis0.bError OR fbHome_Axis1.bError OR fbHome_Axis2.bError OR fbTimeoutTimer.Q THEN
            iHomingState := 99;
        END_IF

    30: // Phase B start: kick off MC_MoveAbsolute on all three.
        // bExecHoming already FALSE from state 20 transition; this scan
        // is the rising edge of bExecZero so the three MC_MoveAbsolute
        // FBs latch their Position parameter and begin the move.
        bExecZero := TRUE;
        fbTimeoutTimer(IN:=TRUE, PT:=UDINT_TO_TIME(udiZeroTimeout));
        iHomingState := 40;

    40: // Phase B wait: all three reached joint 0.
        fbTimeoutTimer(IN:=TRUE, PT:=UDINT_TO_TIME(udiZeroTimeout));

        IF fbZero_Axis0.Done AND fbZero_Axis1.Done AND fbZero_Axis2.Done THEN
            iHomingState := 50;
        ELSIF fbZero_Axis0.Error OR fbZero_Axis1.Error OR fbZero_Axis2.Error OR fbTimeoutTimer.Q THEN
            iHomingState := 99;
        END_IF

    50: // Both phases complete.
        bBusy := FALSE;
        bDone := TRUE;
        bExecZero := FALSE;
        fbTimeoutTimer(IN:=FALSE);

        IF NOT bExecute THEN
            iHomingState := 0;
        END_IF

    99: // Error: switch search or return-to-zero failed.
        bBusy := FALSE;
        bError := TRUE;
        bExecHoming := FALSE;
        bExecZero := FALSE;
        fbTimeoutTimer(IN:=FALSE);

        IF NOT bExecute THEN
            iHomingState := 0;
        END_IF
END_CASE;

// --- Phase A: SMC_Homing instances ---
// bReturnToZero := FALSE so each axis stops at the home switch with
// encoder set to fHomePosition; the synchronized return-to-zero is
// done by phase B below.

fbHome_Axis0(
    Axis:=IoConfig_Globals.EAxis0,
    bExecute:=bExecHoming,
    fHomePosition:=homePose,
    fVelocitySlow:=Speed/10,
    fVelocityFast:=Speed,
    fAcceleration:=Speed*2,
    fDeceleration:=Speed*10,
    fJerk:=Speed*100,
    nDirection:=SM3_Basic.MC_DIRECTION.positive,
    bReferenceSwitch:=IOHUB_mot0_limit=0,
    fSignalDelay:=0,
    nHomingMode:=SM3_Basic.SMC_HOMING_MODE.FAST_BSLOW_S_STOP,
    bReturnToZero:=FALSE,
    bIgnoreHWLimit:=TRUE
);

fbHome_Axis1(
    Axis:=IoConfig_Globals.EAxis1,
    bExecute:=bExecHoming,
    fHomePosition:=homePose,
    fVelocitySlow:=Speed/10,
    fVelocityFast:=Speed,
    fAcceleration:=Speed*2,
    fDeceleration:=Speed*10,
    fJerk:=Speed*100,
    nDirection:=SM3_Basic.MC_DIRECTION.positive,
    bReferenceSwitch:=IOHUB_mot1_limit=0,
    fSignalDelay:=0,
    nHomingMode:=SM3_Basic.SMC_HOMING_MODE.FAST_BSLOW_S_STOP,
    bReturnToZero:=FALSE,
    bIgnoreHWLimit:=TRUE
);

fbHome_Axis2(
    Axis:=IoConfig_Globals.EAxis2,
    bExecute:=bExecHoming,
    fHomePosition:=homePose,
    fVelocitySlow:=Speed/10,
    fVelocityFast:=Speed,
    fAcceleration:=Speed*2,
    fDeceleration:=Speed*10,
    fJerk:=Speed*100,
    nDirection:=SM3_Basic.MC_DIRECTION.positive,
    bReferenceSwitch:=IOHUB_mot2_limit=0,
    fSignalDelay:=0,
    nHomingMode:=SM3_Basic.SMC_HOMING_MODE.FAST_BSLOW_S_STOP,
    bReturnToZero:=FALSE,
    bIgnoreHWLimit:=TRUE
);

// --- Phase B: MC_MoveAbsolute instances, all targeting joint 0 ---
// Execute rising edge happens at state 30 entry, latching Position=0
// on all three so the move starts on the same scan -> synchronized
// return to joint 0.

fbZero_Axis0(
    Axis:=IoConfig_Globals.EAxis0,
    Execute:=bExecZero,
    Position:=0.0,
    Velocity:=ZeroVel,
    Acceleration:=ZeroAcc,
    Deceleration:=ZeroAcc,
    Jerk:=ZeroJerk,
    Direction:=SM3_Basic.MC_DIRECTION.positive
);

fbZero_Axis1(
    Axis:=IoConfig_Globals.EAxis1,
    Execute:=bExecZero,
    Position:=0.0,
    Velocity:=ZeroVel,
    Acceleration:=ZeroAcc,
    Deceleration:=ZeroAcc,
    Jerk:=ZeroJerk,
    Direction:=SM3_Basic.MC_DIRECTION.positive
);

fbZero_Axis2(
    Axis:=IoConfig_Globals.EAxis2,
    Execute:=bExecZero,
    Position:=0.0,
    Velocity:=ZeroVel,
    Acceleration:=ZeroAcc,
    Deceleration:=ZeroAcc,
    Jerk:=ZeroJerk,
    Direction:=SM3_Basic.MC_DIRECTION.positive
);

// Outputs bDone/bError/bBusy are owned by the state machine above.
// Do NOT recompute from axis flags here -- state 99 (timeout) sets
// bError:=TRUE, and axis flags would clobber it back to FALSE on the
// next scan, hiding the timeout from the outer FSM.
