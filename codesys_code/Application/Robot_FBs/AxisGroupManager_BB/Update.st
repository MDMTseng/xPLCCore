METHOD Update : BOOL
VAR_INPUT
	//-1 enter, 0 update,1 exit current(_eState) state
	uidx:INT:=0;
END_VAR
VAR
	
END_VAR
(* =========== IMPLEMENTATION =========== *)
Update:=TRUE;

CASE _eState OF
	E_RobotState.UnInited:
		IF(uidx=-1)THEN//enter
			GroupPower_Flag:=FALSE;
			SMC_GroupDisable(
				AxisGroup:=_agAxisGroup,
				Execute:=FALSE
			);
			
			SMC_GroupReset(
				AxisGroup:=_agAxisGroup,
				Execute:=FALSE
				);
				
			IoConfig_Globals.EtherCAT_Master_SoftMotion.xRestart:=TRUE;
				
			IoConfig_Globals.EtherCAT_Master_SoftMotion();
			(*MotorReset(
			Axis:=IoConfig_Globals.EAxis1,
			Execute:=FALSE);*)
			
		END_IF
		IF(uidx=0)THEN//update
			SMC_GroupDisable(
				AxisGroup:=_agAxisGroup,
				Execute:=TRUE
			);
			SMC_GroupReset(
				AxisGroup:=_agAxisGroup,
				Execute:=TRUE
				);
			IoConfig_Globals.EtherCAT_Master_SoftMotion();
				
				
			IoConfig_Globals.EtherCAT_Master_SoftMotion.xRestart:=FALSE;
				
			(*MotorReset(
			Axis:=IoConfig_Globals.EAxis1,
			Execute:=TRUE);*)
				
		END_IF
		RETURN;
	E_RobotState.Powering:
		IF(uidx=-1)THEN//enter
			GroupPower_Flag:=FALSE;
		ELSIF(uidx=0)THEN
			GroupPower_Flag:=TRUE;
			IF(SMC_GroupPower_0.Status) THEN
				Transition(E_RobotEvent.EV_OK);
			END_IF
		//ELSIF(uidx=1)THEN
			
		END_IF
	//E_RobotState.Powered://a halt state, wait for user 
		//RETURN;
	E_RobotState.GroupEnabling:
		
		IF(uidx=-1)THEN//enter
			enable_fb(
			AxisGroup:=_agAxisGroup,
			Execute:=FALSE,
			);
		ELSIF(uidx=0)THEN
			enable_fb(
			AxisGroup:=_agAxisGroup,
			Execute:=TRUE,
			);
			
			IF(enable_fb.Done) THEN
				Transition(E_RobotEvent.EV_OK);
			END_IF
		END_IF
	
	//E_RobotState.GroupEnabled://a halt state, wait for user's next event
		//RETURN;
	
	E_RobotState.Homing:
		
		IF(uidx=-1)THEN//enter
			homing_fb(
			bExecute:=FALSE,
			);
			stateCD:=100;
		ELSIF(uidx=0)THEN
			
			homing_fb(
			bExecute:=TRUE,
			);
			IF(homing_fb.bDone) THEN
				
				stateCD:=stateCD-1;
				IF(stateCD=0)THEN
					Transition(E_RobotEvent.EV_OK);
				END_IF
			ELSE
				
				stateCD:=100;
			END_IF
			
		ELSIF(uidx=1)THEN
			
			homing_fb(
			bExecute:=FALSE,
			);
			
		END_IF
		
	
	E_RobotState.Ready:
		
		IF(uidx=-1)THEN//enter
		ELSIF(uidx=0)THEN
			uidx:=uidx;

		END_IF
		
		
	E_RobotState.Error:
		
		IF(uidx=-1)THEN//enter
			GroupPower_Flag:=FALSE;
			SMC_GroupDisable(
				AxisGroup:=_agAxisGroup,
				Execute:=FALSE
			);
			
			SMC_GroupReset(
				AxisGroup:=_agAxisGroup,
				Execute:=FALSE
				);
				
			(*MotorReset(
			Axis:=IoConfig_Globals.EAxis1,
			Execute:=FALSE);*)
			
		END_IF
		IF(uidx=0)THEN//enter
			SMC_GroupDisable(
				AxisGroup:=_agAxisGroup,
				Execute:=TRUE
			);
			SMC_GroupReset(
				AxisGroup:=_agAxisGroup,
				Execute:=TRUE
				);
				
			(*MotorReset(
			Axis:=IoConfig_Globals.EAxis1,
			Execute:=TRUE);*)
				
		END_IF
	
END_CASE


SMC_GroupPower_0(
AxisGroup:=_agAxisGroup,
Enable:=TRUE,
bRegulatorOn:=GroupPower_Flag,
bDriveStart:=GroupPower_Flag
);
