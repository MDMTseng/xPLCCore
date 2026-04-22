PROGRAM POU_BUFFER_RUN
VAR CONSTANT
	MOVEMENT_BUFFER_SIZE:UDINT:=50;
END_VAR
VAR
	Axis_PosRead_FB :  ARRAY[0..3] OF MC_ReadActualPosition;
	
	Axis_Homing_FB :  ARRAY[0..3] OF SM3_Basic.SMC_Homing;
	
	
	
    i               : INT;
	OUTPUT_MotPos0:DINT;
	INPUT_MotHome0:BOOL;
	OUTPUT_Y00:BOOL;
	OUTPUT_Y01:BOOL;
	OUTPUT_Y02:BOOL;
	
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
	Waypoints : ARRAY[0..3] OF SM3_Robotics.SMC_POS_REF :=
    [
        ( c := (x := 60.0, y := 0.0, z := 0.0, a := 0.0)),  // Waypoint 0
        ( c := (x := 0.0, y := 60.0, z := 0.0, a := 90.0)), // Waypoint 1
        ( c := (x := -60.0, y := -60.0, z := 0.0, a := 0.0)),  // Waypoint 2
        ( c := (x := 0.0, y := 0.0, z := 0.0, a := 0.0))  // Waypoint 2
		
    ];
	SetBuffer: SMC_SetMovementQueueBuffer;
	
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
	
	
	moid_matcher_buf: ARRAY [0..65] OF SMC_Movement_Id; 
	moid_matcher_ridx:FB_RingBufferIndex(66);
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

//OUTPUT_Y00:=OUTPUT_Y00;
motor_en:=TRUE;



motor_err_rst:=OnBtn_MotErrRst;
Axis_PosRead_FB[0](
	Enable := TRUE,
	Axis := IoConfig_Globals.Axis0,
	Valid => ,
	Error => ,
	ErrorID => ,
	Position =>
);


Axis_PosRead_FB[1](
	Enable := TRUE,
	Axis := IoConfig_Globals.Axis1,
	Valid => ,
	Error => ,
	ErrorID => ,
	Position =>
);

	

Axis_PosRead_FB[2](
	Enable := TRUE,
	Axis := IoConfig_Globals.Axis2,
	Valid => ,
	Error => ,
	ErrorID => ,
	Position =>
);

//OUTPUT_MotPos0:= LREAL_TO_DINT(Axis_PosRead_FB[0].Position/360*AxisMult)+HOMEOFFSET_MotPos0;
//OUTPUT_MotPos1:= LREAL_TO_DINT(Axis_PosRead_FB[1].Position/360*AxisMult);
//OUTPUT_MotPos2:= LREAL_TO_DINT(Axis_PosRead_FB[2].Position/360*AxisMult);

//OUTPUT_MotPos3:= LREAL_TO_DINT(Axis_PosRead_FB[3].Position/360*AxisMult);

(*
IF Axis_PosRead_REAL[3]=0 THEN
	OUTPUT_MotPos3:=0;
ELSE 
	OUTPUT_MotPos3:=OUTPUT_MotPos3+AxisTestPulseCount;
END_IF*)



OUTPUT_Y00:=MotPosIntegral0>-100;


Axis_Homing_FB[0](
Axis:=IoConfig_Globals.Axis0,
bExecute:=DO_HOMING,
//fSignalDelay:=0,
fHomePosition:=0,
fVelocityFast:=5,
fVelocitySlow:=1,
fAcceleration:=15,
fDeceleration:=15,
fJerk:=1,
bReturnToZero:=TRUE,
nDirection:=SM3_Basic.MC_DIRECTION.negative,
bReferenceSwitch:=INPUT_MotHome0,//HOMING_SENSOR,//NOT INPUT_MotHome0,
nHomingMode:=SM3_Basic.SMC_HOMING_MODE.FAST_BSLOW_STOP_S,
bIndexOccured:=HOMING_INDEX,

bIgnoreHWLimit:=TRUE,
bDone=>HOMING_DONE,
bBusy=>HOMING_BUSY


);


IF HOMING_DONE THEN
	OUTPUT_ResetPos0:=TRUE;
	OUTPUT_MotPos0:=0;
	pre_MotPos0:=0;
	
	
	DO_HOMING:=FALSE;
ELSE
	OUTPUT_ResetPos0:=FALSE;
	MotPosIntegral0:=MotPosIntegral0+OUTPUT_MotPos0-pre_MotPos0;
	//OUTPUT_ResetPos0:=FALSE;
	pre_MotPos0:=OUTPUT_MotPos0;
END_IF

HOMEOFFSET_MotPos0:=0;

//OUTPUT_Y00:=(IOGapCounter AND (1024-1))<2;
IF INPUT_MotHome0 THEN
	
   OUTPUT_MotPos3:=OUTPUT_MotPos3+10;
END_IF
OUTPUT_Y01:=INPUT_MotHome0;

OUTPUT_Y02:=INPUT_MotHome0;

IOGapCounter:=IOGapCounter+1;


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



MC_GroupReadStatus_0(
	AxisGroup          := AxisGroupGVL_agSpiderR.SpiderR,
	Enable            := 1,
	ActiveMovementId =>ActiveMovementId
	
	);

IF(ActiveMovementId<>0 AND preActiveMovementId<>ActiveMovementId)THEN 
	readMovementId:=moid_matcher_buf[moid_matcher_ridx.getTail()];
	moid_matcher_ridx.consumeTail();
END_IF

preActiveMovementId:=ActiveMovementId;
	