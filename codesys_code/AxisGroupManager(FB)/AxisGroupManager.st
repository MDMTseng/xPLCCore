
//==============================================================================
// Main Function Block Definition
//==============================================================================
FUNCTION_BLOCK AxisGroupManager

VAR
    // --- Internal State & Events ---
    _preState : E_RobotState := E_RobotState.NULL;
    _eState : E_RobotState := E_RobotState.NULL;
    _nxtState : E_RobotState := E_RobotState.NULL;
	
    _eCurrentEvent : E_RobotEvent := E_RobotEvent.EV_NONE; // Holds the event to be processed

    // --- Axis Configuration ---
    _agAxisGroup : REFERENCE TO AXIS_GROUP_REF_SM3; // Internal reference to the axis group
    _axReel      : REFERENCE TO AXIS_REF_SM3;       // Internal reference to the single (reel) axis
   
    // --- Status & Error Info ---
    _bIsHomed : BOOL;
    _bIsInError : BOOL;
    _dwErrorID : DWORD;
    _bReadyForOperation : BOOL;

    // --- Motion Control Function Blocks ---
    _fbPower : MC_Power;
    _fbGroupEnable : MC_GroupEnable;
    _fbGroupReset : MC_GroupReset;
    _fbGroupDisable : MC_GroupDisable;
    _fbHome : MC_Home;
	
	
	
	fbPowerReel   : MC_Power;
    fbResetReel   : MC_Reset;
    bReelPowerReq : BOOL; // Flag to request power for the reel motor
	SMC_GroupPower_0:SM3_Robotics.SMC_GroupPower;
	GroupPower_Flag:BOOL:=FALSE;
	//SMC_GroupReset_0:SM3_Robotics.MC_GroupReset;
	SMC_GroupDisable:SM3_Robotics.MC_GroupDisable;
	
	SMC_GroupReset:SM3_Robotics.MC_GroupReset;
	
	enable_fb:SM3_Robotics.MC_GroupEnable;
	
	homing_fb:FB_Homing;
	
	
	MoveLinearAbsolute_0: SM3_Robotics.MC_MoveLinearAbsolute;
	motionPoseRunPass:INT;
	motionPoseIdx:INT;
	
	stateCD:INT;

END_VAR

// Cyclic driver: advances in-state logic every scan.
// External callers post user events via Transition(eEvent);
// EV_OK / EV_ERROR are posted from inside Update(0) when Done/Error flags latch.
Update(0);
