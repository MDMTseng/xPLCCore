
//==============================================================================
// Main Function Block Definition
//==============================================================================
FUNCTION_BLOCK AxisGroupManager

VAR
    // --- Internal State & Events ---
    _preState : E_RobotState := E_RobotState.NULL;
    _eState : E_RobotState := E_RobotState.NULL;
    _nxtState : E_RobotState := E_RobotState.NULL;

    // --- Axis Configuration ---
    _agAxisGroup : REFERENCE TO AXIS_GROUP_REF_SM3; // Internal reference to the axis group
    _axReel      : REFERENCE TO AXIS_REF_SM3;       // Internal reference to the single (reel) axis

    // Error latch lives in GVL (GVL.LastErrorSource / GVL.LastErrorID) so
    // that both this FB (internal writes from Update) and external
    // supervisors (AxisGroupSM) can populate it without needing method/
    // property plumbing.

    // --- Motion Control Function Blocks ---
    fbPowerReel   : MC_Power;
    fbResetReel   : MC_Reset;
    bReelPowerReq : BOOL; // Flag to request power for the reel motor
    SMC_GroupPower_0:SM3_Robotics.SMC_GroupPower;
    GroupPower_Flag:BOOL:=FALSE;
    SMC_GroupDisable:SM3_Robotics.MC_GroupDisable;
    SMC_GroupReset:SM3_Robotics.MC_GroupReset;
    enable_fb:SM3_Robotics.MC_GroupEnable;
    homing_fb:FB_Homing;

    // State-local countdown timers (replaced the single `stateCD` shared
    // between Homing-done debounce and Ready-entry reset hold -- having
    // one variable for two unrelated purposes was a foot-gun).
    HomingDoneDebounceCD : INT; // homing_fb.bDone debounce in Homing
    ReadyResetHoldCD     : INT; // SMC_GroupReset hold scans on Ready entry

	// State-entry watchdogs. Reset on entry (IN:=FALSE in uidx=-1), driven
	// IN:=TRUE during the state's update. If .Q latches before the state
	// completes, fire EV_ERROR with source=<state>:timeout. Timeouts chosen
	// generously to tolerate normal hardware latency but still catch a
	// stuck bus / silent drive / missed Done latch.
	watchdogPowering      : Standard.TON;
	watchdogGroupEnabling : Standard.TON;
	watchdogHoming        : Standard.TON;
	WATCHDOG_POWERING_PT      : TIME := T#10S;
	WATCHDOG_GROUP_ENABLE_PT  : TIME := T#10S;
	WATCHDOG_HOMING_PT        : TIME := T#30S;

END_VAR
(* =========== IMPLEMENTATION =========== *)

// FB body is intentionally empty: nothing calls AxisGroupManagerFb() as
// a procedure. The cyclic Update(0) tick is invoked explicitly via
// `AxisGroupManagerFb.Update();` in UpdateAxisGroupState.st so the
// caller stays in control of WHERE in the scan the FSM advances
// (between TCP rx drain and motion dispatch). User events come in via
// `AxisGroupManagerFb.Transition(eEvent);` from VisuEventControl edge
// detect, DrainHostPackets GA_EV, and internal Update Trip helpers.
