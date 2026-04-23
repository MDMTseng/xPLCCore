FUNCTION_BLOCK FB_Homing
VAR_INPUT
	
    bExecute : BOOL; // Comes from the Homing step being active
    udiTimeout : UDINT := 30000; // Homing timeout in milliseconds (default: 30s)
END_VAR
VAR_OUTPUT
    bDone : BOOL;     // Sets GVL_Robot.bHomingComplete
    bError : BOOL;    // Sets GVL_Robot.bError
    bBusy : BOOL;     // TRUE while the block is working
END_VAR
VAR
    // Assuming you have 3 axes
    fbHome_Axis0 : SM3_Basic.SMC_Homing;
    fbHome_Axis1 : SM3_Basic.SMC_Homing;
    fbHome_Axis2 : SM3_Basic.SMC_Homing;

    // State machine and timeout control
    iHomingState : INT := 0;
    fbTimeoutTimer : TON;
    bStartHoming : BOOL; // Internal trigger for sub-blocks
	inputSwitches:BYTE;
END_VAR

// --- Local Constants for Homing Parameters ---
VAR CONSTANT
    Speed: REAL := 10;
	
	homePose: REAL := 20.0;
END_VAR
(* =========== IMPLEMENTATION =========== *)
(*
    Implementation with State Machine and Timeout
*)

//inputSwitches:=IoConfig_Globals.HECAT_1616.InputData^;//////////////////////////////////////////////

// The CASE statement handles the homing sequence state.
CASE iHomingState OF
    0: // Idle state
        bBusy := FALSE;
        bDone := FALSE;
        bError := FALSE;
        bStartHoming := FALSE; // Ensure sub-blocks are not executing

        IF bExecute THEN
            iHomingState := 10; // Start the process
        END_IF

    10: // Start homing and activate timer
        bBusy := TRUE;
        bStartHoming := TRUE; // Trigger all axes to start homing
        fbTimeoutTimer(IN:=TRUE, PT:=UDINT_TO_TIME(udiTimeout)); // Start the timer
        iHomingState := 20;

    20: // Monitor homing progress
        fbTimeoutTimer(IN:=TRUE, PT:=UDINT_TO_TIME(udiTimeout)); // Keep timer running

        // Check for successful completion
        IF fbHome_Axis0.bDone AND fbHome_Axis1.bDone AND fbHome_Axis2.bDone THEN
            iHomingState := 30; // Success
        
        // Check for an error from any axis OR a timeout
        ELSIF fbHome_Axis0.bError OR fbHome_Axis1.bError OR fbHome_Axis2.bError OR fbTimeoutTimer.Q THEN
            iHomingState := 99; // Failure
        END_IF

    30: // Homing successful
        bBusy := FALSE;
        bDone := TRUE;
        //bStartHoming := FALSE; // Stop executing
        fbTimeoutTimer(IN:=FALSE); // Reset timer

        IF NOT bExecute THEN // Wait for external signal to reset
            iHomingState := 0;
        END_IF

    99: // Error state (from axis error or timeout)
        bBusy := FALSE;
        bError := TRUE;
        bStartHoming := FALSE; // IMPORTANT: This stops all fbHome blocks
        fbTimeoutTimer(IN:=FALSE); // Reset timer

        IF NOT bExecute THEN // Wait for external signal to reset
            iHomingState := 0;
        END_IF
END_CASE;

// --- Call Homing Blocks for Each Axis ---
// The bStartHoming variable now controls execution, allowing the state machine to stop them on error/timeout.
fbHome_Axis0(
    Axis:=IoConfig_Globals.EAxis0,
    bExecute:=bStartHoming,
    fHomePosition:=homePose,

	fVelocitySlow:=Speed/10,
	
	fVelocityFast:=Speed,
	
	fAcceleration:=Speed*2,
	
	fDeceleration:=Speed*10,
	
	fJerk:=Speed*100,
	
	nDirection:=SM3_Basic.MC_DIRECTION.positive,
	
	bReferenceSwitch:=IOHUB_mot0_limit=0,//(inputSwitches AND 1)=0,
	
	fSignalDelay:=0,
	
	nHomingMode:=SM3_Basic.SMC_HOMING_MODE.FAST_BSLOW_S_STOP,
	
	bReturnToZero:=TRUE,
	
	bIgnoreHWLimit:=TRUE


);

fbHome_Axis1(
    Axis:=IoConfig_Globals.EAxis1,
    bExecute:=bStartHoming,
    fHomePosition:=homePose,
fVelocitySlow:=Speed/10,

fVelocityFast:=Speed,

fAcceleration:=Speed*2,

fDeceleration:=Speed*10,

fJerk:=Speed*100,

nDirection:=SM3_Basic.MC_DIRECTION.positive,

bReferenceSwitch:=IOHUB_mot1_limit=0,//(inputSwitches AND 2)=0,

fSignalDelay:=0,

nHomingMode:=SM3_Basic.SMC_HOMING_MODE.FAST_BSLOW_S_STOP,

bReturnToZero:=TRUE,

bIgnoreHWLimit:=TRUE


);





fbHome_Axis2(
    Axis:=IoConfig_Globals.EAxis2,
    bExecute:=bStartHoming,
    fHomePosition:=homePose,

fVelocitySlow:=Speed/10,

fVelocityFast:=Speed,

fAcceleration:=Speed*2,

fDeceleration:=Speed*10,

fJerk:=Speed*100,

nDirection:=SM3_Basic.MC_DIRECTION.positive,

bReferenceSwitch:=IOHUB_mot2_limit=0,

//(inputSwitches AND 4)=0,

fSignalDelay:=0,

nHomingMode:=SM3_Basic.SMC_HOMING_MODE.FAST_BSLOW_S_STOP,

bReturnToZero:=TRUE,

bIgnoreHWLimit:=TRUE


);

// Outputs bDone/bError/bBusy are owned by the state machine above.
// Do NOT recompute from axis flags here -- state 99 (timeout) sets
// bError:=TRUE, and axis flags would clobber it back to FALSE on the
// next scan, hiding the timeout from the outer FSM.