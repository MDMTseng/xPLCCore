METHOD Update_1 : BOOL
VAR_INPUT
	//-1 enter, 0 update,1 exit current(_eState) state
	uidx:INT:=0;
END_VAR
VAR
	
RunSpeed:REAL:=100000;
RunFactor:REAL:=1;

	motionPoses:ARRAY[0..3] OF  SM3_Robotics.SMC_POS_REF :=
    [ ( c := (x := 60.0, y := -60.0, z := -10.0, a := 0.0)),
	 ( c := (x := 60.0, y := 60.0, z := -10.0, a := 0.0)),
	  ( c := (x := -60.0, y := 60.0, z := -10.0, a := 0.0)),
	   ( c := (x := -60.0, y := -60.0, z := -10.0, a := 0.0))

	];
	TransitionParameter:ARRAY [0..1]OF LREAL:=[2,0];
END_VAR
(* =========== IMPLEMENTATION =========== *)
Update_1:=TRUE;

CASE _eState OF
	E_RobotState.UnInited:
	    IF(uidx=1)THEN
			//EXIT, you may do some init stuff here
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
			motionPoseIdx:=0;
			motionPoseRunPass:=0;
		ELSIF(uidx=0)THEN
			
			MoveLinearAbsolute_0(
			AxisGroup          := _agAxisGroup,
			Execute            := 0
			);
				
				
			IF(motionPoseIdx>3)THEN
				IF(motionPoseRunPass<1)THEN
					motionPoseRunPass:=motionPoseRunPass+1;
					motionPoseIdx:=0;
				END_IF
			ELSE
					
		
				MoveLinearAbsolute_0(
				AxisGroup          := _agAxisGroup,
				Execute            := 1,
				Position           := motionPoses[motionPoseIdx],              // ← [0.0,0.0,0.0,0.0]
				Velocity           := RunSpeed,                 // 速度 (mm/s 或 deg/s)
				Acceleration       := RunSpeed,                // 加速度 (mm/s² 或 deg/s²)
				Deceleration       := RunSpeed, 
				Jerk               := RunSpeed*5, 
				CoordSystem		   := SMC_COORD_SYSTEM.MCS,
				
				
				BufferMode         := SM3_Common.MC_BUFFER_MODE.BlendingHigh, // 緩衝模式
				TransitionMode     := SM3_Robotics.MC_TRANSITION_MODE.TMCornerDistance,
				TransitionParameter:= TransitionParameter,
				
				//BufferMode         := SM3_Common.MC_BUFFER_MODE.Buffered,
				//TransitionMode     := SM3_Robotics.MC_TRANSITION_MODE.TMNone,
				
				
				
				
				VelFactor:=RunFactor,
				AccFactor:=RunFactor,
				JerkFactor:=RunFactor,
				TorqueFactor:=1,
				Done=>,
				Busy=>,
				Active=>,
				Error=>,
				ErrorID=>,
				MovementId=>
				);
				
				motionPoseIdx:=motionPoseIdx+1;
			
			END_IF
		

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
				
		END_IF
	
END_CASE


SMC_GroupPower_0(
AxisGroup:=_agAxisGroup,
Enable:=TRUE,
bRegulatorOn:=GroupPower_Flag,
bDriveStart:=GroupPower_Flag
);
