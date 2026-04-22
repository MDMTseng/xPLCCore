FUNCTION MsgPakInfoWrapup : BOOL
VAR_IN_OUT
	msgpak:MsgPakInfo;
END_VAR
VAR
	mpPacker: FB_MpPacker;
END_VAR
(* =========== IMPLEMENTATION =========== *)
IF msgpak.useLargeElementCountField=FALSE AND msgpak.ele_count>15 THEN//the size field does not fit
	MsgPakInfoWrapup:=FALSE;
	//TODO: it's possible to move buffer around 
	RETURN;
END_IF



mpPacker.PackMapHeader(pStart := msgpak.buf, uiCount := TO_UINT(msgpak.ele_count), bForceLargeField := msgpak.useLargeElementCountField);

MsgPakInfoWrapup:=TRUE;

msgpak.buf_size:=TO_UDINT(msgpak.whead-msgpak.buf);