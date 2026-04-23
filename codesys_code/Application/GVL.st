{attribute 'qualified_only'}
VAR_GLOBAL
	
	
	minfo_buf: ARRAY [0..5] OF ARRAY [0..256-1]OF BYTE; 
	minfo_buf_ridx:FB_RingBufferIndex(6);
	
	
	aux0_info_buf: ARRAY [0..5] OF ARRAY [0..256-1]OF BYTE; 
	aux0_info_buf_ridx:FB_RingBufferIndex(6);
	aux1_info_buf: ARRAY [0..5] OF ARRAY [0..256-1]OF BYTE; 
	aux1_info_buf_ridx:FB_RingBufferIndex(6);
	aux2_info_buf: ARRAY [0..5] OF ARRAY [0..256-1]OF BYTE; 
	aux2_info_buf_ridx:FB_RingBufferIndex(6);
	
	reMP_info_SIZE:INT:=32;
	reMP_info_buff_SIZE:INT:=256;
	reMP_info_buf: ARRAY [0..32-1] OF ARRAY [0..256-1]OF BYTE; 
	reMP_info_arr: ARRAY [0..32-1] OF MsgPakInfo; 
	reMP_info_ridx:FB_RingBufferIndex(32);


	(* --- MsgPack self-test harness (bypasses web UI).
	   Toggle bRunMsgPackTests in online mode; read outputs on the FB
	   instance. See doc/msgpack.md for expected failures on today's
	   code. *)
	bRunMsgPackTests : BOOL;
	fbMsgPackTests   : FB_MsgPackTests;

	(* --- Virtual-motors safety gate.
	   bVirtualMotorsMode_Request is the FORCEABLE knob (non-retained, cold
	   boot resets to FALSE). The PLC derives bVirtualMotorsMode from it
	   AND a 10-minute TTL timer; the TTL self-expires even if the force
	   is left on, so a forgotten override can't silently persist into a
	   real-motor session. EV_HOME_GO_FORCE_SKIP only fires when the
	   derived flag is TRUE.
	   To use: force bVirtualMotorsMode_Request := TRUE in online mode,
	   then drive the SYS event sequence. On cleanup: unforce_all_values. *)
	bVirtualMotorsMode_Request : BOOL;
	bVirtualMotorsMode         : BOOL;
	fbVirtualMotorsExpire      : Standard.TON;
	VIRTUAL_MOTORS_TTL         : TIME := T#10M;

END_VAR
