//==============================================================================
// FB_init: Called once on startup to configure the FB instance.
//==============================================================================
METHOD FB_init : BOOL
VAR_INPUT
    bInitRetains : BOOL; // This parameter is required by the compiler
    bInCopyCode : BOOL;  // This parameter is required by the compiler

    // The robot's axis group
    agAxisGroup : REFERENCE TO AXIS_GROUP_REF_SM3;

END_VAR
VAR
    i : UINT;
END_VAR




// Store the reference to the axis group
_agAxisGroup REF= agAxisGroup;

FB_init := TRUE;

_eState:=E_RobotState.NULL;
_nxtState:=E_RobotState.UnInited;
Update(1);//exit old state
_preState:=_eState;
_eState:=_nxtState;
Update(-1);//enter new state