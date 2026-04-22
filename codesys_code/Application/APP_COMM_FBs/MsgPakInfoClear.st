FUNCTION MsgPakInfoClear : BOOL
VAR_IN_OUT
	msgpak:MsgPakInfo;
END_VAR
VAR
	
END_VAR
(* =========== IMPLEMENTATION =========== *)


msgpak:=MsgPakInfoInit(msgpak.buf,msgpak.buf_capacity,msgpak.useLargeElementCountField);