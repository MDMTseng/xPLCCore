METHOD Transition : BOOL
VAR_INPUT
    eEvent : E_RobotEvent;
END_VAR
VAR_OUTPUT
	error:BOOL;
	new_state:E_RobotState;
END_VAR
(* =========== IMPLEMENTATION =========== *)



new_state:=_eState;
CASE _eState OF
	E_RobotState.UnInited:
		CASE eEvent OF
			E_RobotEvent.EV_POWER_ON:
			 new_state:=E_RobotState.Powering;
		END_CASE
		
	E_RobotState.Powering:
		CASE eEvent OF
			E_RobotEvent.EV_POWER_OFF,
			E_RobotEvent.EV_ERROR,
			E_RobotEvent.EV_RESET:
			   new_state:=E_RobotState.UnInited;
			E_RobotEvent.EV_OK:
			   new_state:=E_RobotState.Powered;
		END_CASE
	E_RobotState.Powered:
		CASE eEvent OF
		E_RobotEvent.EV_GROUP_ENABLE:
			 new_state:=E_RobotState.GroupEnabling;
		END_CASE
		
	E_RobotState.GroupEnabling:
		CASE eEvent OF
		E_RobotEvent.EV_OK:
			 new_state:=E_RobotState.GroupEnabled;
		END_CASE
		
	
	E_RobotState.GroupEnabled:
		CASE eEvent OF
		E_RobotEvent.EV_HOME_GO:
			 new_state:=E_RobotState.Homing;
		E_RobotEvent.EV_HOME_GO_FORCE_SKIP:
			 IF GVL.bVirtualMotorsMode THEN
			     new_state:=E_RobotState.Ready;
			 END_IF
		END_CASE
	E_RobotState.Homing:
		CASE eEvent OF
		E_RobotEvent.EV_OK:
			 new_state:=E_RobotState.Ready;
		END_CASE
	
	
	E_RobotState.Ready:
		CASE eEvent OF
		E_RobotEvent.EV_HOME_GO:
			 new_state:=E_RobotState.Homing;
		END_CASE
	
	E_RobotState.Error:
		CASE eEvent OF
		E_RobotEvent.EV_RESET:
			 new_state:=E_RobotState.UnInited;
		END_CASE
	
	
	
END_CASE

IF(new_state=_eState)THEN//error/stop handling
	IF(eEvent=E_RobotEvent.EV_ERROR)THEN
		new_state:=E_RobotState.Error;
	ELSIF(eEvent=E_RobotEvent.EV_RESET)THEN
		// Universal reset: any state snaps back to UnInited. Lets operators
		// (and smoketests) re-run the FSM sequence without a cold boot.
		new_state:=E_RobotState.UnInited;
	END_IF

END_IF

IF(new_state<>_eState)THEN
  _nxtState:=new_state;
  Update(1);//exit old state
  _preState:=_eState;
  _eState:=_nxtState;
  Update(-1);//enter new state
  
END_IF