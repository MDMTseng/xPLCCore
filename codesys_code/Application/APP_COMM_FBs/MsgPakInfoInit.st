FUNCTION MsgPakInfoInit : MsgPakInfo
VAR_INPUT
	buff:POINTER TO BYTE;
	buffer_size:UDINT;
	useLargeElementCountField:BOOL := TRUE;
END_VAR
VAR
	
	retStruct:MsgPakInfo;
	
	mpPacker: FB_MpPacker;
END_VAR
(* =========== IMPLEMENTATION =========== *)


retStruct.buf_capacity:=buffer_size;
retStruct.buf:=buff;
retStruct.useLargeElementCountField:=useLargeElementCountField;
retStruct.ele_count:=0;
retStruct.buf_size:=0;
retStruct.whead := mpPacker.PackMapHeader(pStart := buff, uiCount := TO_UINT(0), bForceLargeField := useLargeElementCountField); // just a place holder
MsgPakInfoInit:=retStruct;