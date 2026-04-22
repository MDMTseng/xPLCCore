PROGRAM performanceTestPOU
VAR
	SMC_TRAFOF_Tripod_Arm_0: SM3_CNC.SMC_TRAFOF_Tripod_Arm;
	SMC_TRAFOF_Tripod_Arm_inv: SM3_CNC.SMC_TRAFO_Tripod_Arm;
	iCounter:INT;
	
	poseInfo:SM3_CNC.SMC_POSINFO;
END_VAR
(* =========== IMPLEMENTATION =========== *)




FOR iCounter := 1 TO 100 BY 1 DO
SMC_TRAFOF_Tripod_Arm_0(
	dArmLength1:=125,
	dArmLength2:=240,
	dArm1Radius:=55,
	dStewartRadius:=41.03657,
	dDistance:=56,
	dRotationOffset:=0,
	DriveA:=IoConfig_Globals.Axis0,
	DriveB:=IoConfig_Globals.Axis1,
	DriveC:=IoConfig_Globals.Axis2,
	dx=>poseInfo.dX,
	dy=>poseInfo.dY,
	dz=>poseInfo.dZ
	);

SMC_TRAFOF_Tripod_Arm_inv(
	pi:=poseInfo,
dArmLength1:=125,
	dArmLength2:=240,
	dArm1Radius:=55,
	dStewartRadius:=41.03657,
	dDistance:=56,
	dRotationOffset:=0,
	dMaxAngleBallJoint:=45
	);
END_FOR;