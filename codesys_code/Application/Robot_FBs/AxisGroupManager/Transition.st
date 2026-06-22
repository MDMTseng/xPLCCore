METHOD Transition : BOOL
VAR_INPUT
    eEvent : E_RobotEvent;
END_VAR
VAR_OUTPUT
	error:BOOL;
	new_state:E_RobotState;
END_VAR
VAR
	xForceReentry: BOOL;
END_VAR
(* =========== IMPLEMENTATION =========== *)



xForceReentry := FALSE;
new_state:=_eState;
CASE _eState OF
	E_RobotState.UnInited:
		CASE eEvent OF
			E_RobotEvent.EV_POWER_ON:
			 new_state:=E_RobotState.Powering;
		END_CASE
		
	E_RobotState.Powering:
		CASE eEvent OF
			E_RobotEvent.EV_OK:
			   new_state:=E_RobotState.Powered;
		END_CASE
		// EV_ERROR / EV_RESET intentionally NOT listed in the CASE above.
		// They fall through to the universal handler below:
		//   EV_ERROR -> Error  (controlled GroupStop fires; st=990 visible
		//                       to host so recovery UI engages)
		//   EV_RESET -> UnInited (universal reset, any state)
		// Pre-2026-06-23 code review: this CASE mapped EV_ERROR to
		// UnInited, so a fault during power-ramp (e.g. EtherCAT bus drop)
		// silently looked like a normal cold-down. GroupStopExecute is
		// gated on _eState=Error so the controlled stop never ran, and
		// the host's Error-state recovery path never engaged.
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
		// Audit-fix: EV_ERROR while already in Error must re-execute the
		// entry actions (clear coord gate, re-pulse SMC_GroupDisable/Reset,
		// restamp LastErrorSource) so a stacked second fault doesn't get
		// masked by the first. Pre-fix new_state stayed Error and the
		// `IF(new_state<>_eState)` guard below skipped Update(-1).
		IF (_eState = E_RobotState.Error) THEN
			xForceReentry := TRUE;
		ELSE
			new_state:=E_RobotState.Error;
		END_IF
	ELSIF(eEvent=E_RobotEvent.EV_RESET)THEN
		// Universal reset: any state snaps back to UnInited. Lets operators
		// (and smoketests) re-run the FSM sequence without a cold boot.
		// Re-pulse entry actions even on UnInited self-loop -- otherwise an
		// operator hitting EV_RESET twice in a row to clear lingering state
		// has no effect on the second press.
		IF (_eState = E_RobotState.UnInited) THEN
			xForceReentry := TRUE;
		ELSE
			new_state:=E_RobotState.UnInited;
		END_IF
	END_IF

END_IF

IF(new_state<>_eState)THEN
  _nxtState:=new_state;
  Update(1);//exit old state
  _preState:=_eState;
  _eState:=_nxtState;
  Update(-1);//enter new state

ELSIF xForceReentry THEN
  // Same-state re-entry path. _preState/_eState unchanged; just re-run
  // exit-then-entry so the entry-side cleanup fires fresh.
  Update(1);
  Update(-1);
  GVL.SelfReentryCount := GVL.SelfReentryCount + 1;
END_IF