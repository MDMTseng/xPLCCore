METHOD TripPowerError : BOOL
VAR_INPUT
    psStateName : POINTER TO STRING;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Latch GVL.LastErrorSource + LastErrorID from whichever of the two
// power FBs flagged an error, then post EV_ERROR to drive the FSM
// into Error state. Used by Powering / Powered / GroupEnabled / Ready
// to dedupe the 8-line "which FB tripped + stamp + Transition" block
// that previously appeared verbatim in each.
// Caller still owns the OR-detection at the call site so the helper
// runs only when there IS an error -- no wasted CONCAT on the hot path.
IF SMC_GroupPower_0.Error THEN
    GVL.LastErrorSource := CONCAT(psStateName^, ':SMC_GroupPower');
    GVL.LastErrorID := TO_DWORD(SMC_GroupPower_0.ErrorID);
ELSE
    GVL.LastErrorSource := CONCAT(psStateName^, ':fbPowerReel');
    GVL.LastErrorID := TO_DWORD(fbPowerReel.ErrorID);
END_IF
Transition(E_RobotEvent.EV_ERROR);
TripPowerError := TRUE;
