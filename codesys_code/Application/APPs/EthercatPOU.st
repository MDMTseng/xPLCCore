PROGRAM EthercatPOU


VAR CONSTANT
	MOVEMENT_BUFFER_SIZE:UDINT:=50;
END_VAR
VAR
    i               : INT;
	
	
	SRGapCounter:INT;
	OUTPUT_Y00:BOOL;
	OUTPUT_Y01:BOOL;
	OUTPUT_Y02:BOOL;
	
	
	INPUT_X00:BOOL;
	INPUT_X01:BOOL;
	INPUT_X02:BOOL;
	
	
	Axis_PosRead_FB :  ARRAY[0..3] OF MC_ReadActualPosition;
	
	Axis_Homing_FB :  ARRAY[0..3] OF SM3_Basic.SMC_Homing;
	
	
	OUTPUT_MotPos0:DINT;
	INPUT_MotHome0:BOOL;
	
	OUTPUT_MotPos1:DINT;
	OUTPUT_MotPos2:DINT;
	OUTPUT_MotPos3:DINT;
	
	pre_MotPos0:DINT;
	
	HOMEOFFSET_MotPos0:DINT;
	OUTPUT_ResetPos0:BOOL:=FALSE;
	OUTPUT_ResetPos1:BOOL:=FALSE;
	OUTPUT_ResetPos2:BOOL:=FALSE;
	OUTPUT_ResetPos3:BOOL:=FALSE;
	
	
	
	motor_err_rst:BOOL:=TRUE;
	motor_en:BOOL:=TRUE;
	OnBtn_MotErrRst:BOOL;
	OnRunBtnTrigger:BOOL;
	index:INT:=0;
	
	AxisMult:DINT:=10000;
	
	AxisTestPulseCount:DINT:=100;
	
	MoveLinearAbsolute_0: SM3_Robotics.MC_MoveLinearAbsolute;
	//SetBuffer: SMC_SetMovementQueueBuffer;
	
    MovementBuffer: ARRAY[0..MOVEMENT_BUFFER_SIZE-1] OF SMC_Movement;  // 16 movements buffer
	
	motionPose:SM3_Robotics.SMC_POS_REF :=( c := (x := 60.0, y := 0.0, z := 0.0, a := 0.0));
	tmp_ptr:POINTER TO BYTE;
	cmd_type:STRING;
	fb_MsgUnPak : FB_MpUnpacker;
	TransitionParameter:ARRAY [0..1]OF LREAL:=[7,0];
	wait:ton;
	
	MOTION_ERR:BOOL;
	MOTION_ERR_ID:INT;
	MovementId:SMC_Movement_Id;
	latest_ACC_MovementId:SMC_Movement_Id;
	
	MG_State:INT;
	
	
	MC_GroupReadStatus_0: SM3_Robotics.MC_GroupReadStatus;
	ActiveMovementId:SMC_Movement_Id;
	preActiveMovementId:SMC_Movement_Id;
	
	retryMCMDCD:INT:=0;
	
	
	moid_matcher_buf: ARRAY [0..15] OF SMC_Movement_Id; 
	moid_matcher_ridx:FB_RingBufferIndex(16);
	
	
	
	readMovementId:SMC_Movement_Id;
	MsgPakQSize:UDINT:=0;
	
	RunSpeed:REAL:=300;
	RunFactor:REAL:=1;
	
	DO_HOMING:BOOL:=FALSE;
	HOMING_SENSOR:BOOL:=FALSE;
	
	HOMING_INDEX:BOOL;
	HOMING_DONE:BOOL;
	HOMING_BUSY:BOOL;
	
	
	MotPosIntegral0:DINT:=0;
	IOGapCounter:DINT:=0;
END_VAR
(* =========== IMPLEMENTATION =========== *)
i:=i+1;


IF (i AND 63)=0 THEN
	OUTPUT_Y00:=NOT OUTPUT_Y00;
	SRGapCounter:=0;
END_IF

OUTPUT_Y01:=INPUT_X00;


IF(OUTPUT_Y00<>INPUT_X00)THEN
	SRGapCounter:=SRGapCounter+1;
END_IF

//Application.EthercatPOU.OUTPUT_Y00





MC_GroupReadStatus_0(
	AxisGroup          := AxisGroupGVL_agSpiderR.SpiderR,
	Enable             := 1,
	ActiveMovementId =>ActiveMovementId
	
	);



MoveLinearAbsolute_0(
	AxisGroup          := AxisGroupGVL_agSpiderR.SpiderR,
	Execute            := 0);


MG_State:=AxisGroupGVL_agSpiderR.SpiderR.m_state;

IF(retryMCMDCD>0)THEN retryMCMDCD:=retryMCMDCD-1; END_IF
MsgPakQSize:=GVL.minfo_buf_ridx.size();
IF(retryMCMDCD=0 AND GVL.minfo_buf_ridx.size()>0 AND moid_matcher_ridx.size()<7)THEN
	tmp_ptr:=ADR(GVL.minfo_buf[GVL.minfo_buf_ridx.getTail()][0]);
	fb_MsgUnPak.Init(ADR(tmp_ptr[1]),tmp_ptr[0]);
	
	
							
							
								
	cmd_type:=fb_MsgUnPak.TryReadString('cmd','');

	IF(cmd_type='G1')THEN
		motionPose.c.X:=fb_MsgUnPak.TryReadREAL('X',motionPose.c.X);
		motionPose.c.Y:=fb_MsgUnPak.TryReadREAL('Y',motionPose.c.Y);
		motionPose.c.Z:=fb_MsgUnPak.TryReadREAL('Z',motionPose.c.Z);
		motionPose.c.A:=fb_MsgUnPak.TryReadREAL('A',motionPose.c.A);
		
		
		

		MoveLinearAbsolute_0(
		AxisGroup          := AxisGroupGVL_agSpiderR.SpiderR,
		Execute            := 1,
		Position           := motionPose,              // ← [0.0,0.0,0.0,0.0]
		Velocity           := RunSpeed*3,                 // 速度 (mm/s 或 deg/s)
		Acceleration       := RunSpeed,                // 加速度 (mm/s² 或 deg/s²)
		Deceleration       := RunSpeed, 
		Jerk               := RunSpeed/3, 
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
		Error=>MOTION_ERR,
		ErrorID=>MOTION_ERR_ID,
		MovementId=>MovementId
		);
		
		
		

		IF(MoveLinearAbsolute_0.CommandAccepted)THEN
	    	GVL.minfo_buf_ridx.consumeTail();
			latest_ACC_MovementId:=MovementId;
			
			moid_matcher_buf[moid_matcher_ridx.getHead()]:=latest_ACC_MovementId;
			moid_matcher_ridx.pushHead();
		ELSE
			retryMCMDCD:=50;
		END_IF
		

	ELSE
	    GVL.minfo_buf_ridx.consumeTail();
	END_IF
	
		
	
END_IF




IF(ActiveMovementId<>0 AND preActiveMovementId<>ActiveMovementId)THEN 
	readMovementId:=moid_matcher_buf[moid_matcher_ridx.getTail()];
	moid_matcher_ridx.consumeTail();
END_IF

preActiveMovementId:=ActiveMovementId;
	