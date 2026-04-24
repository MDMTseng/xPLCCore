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

	(* --- Error diagnostic latch.
	   Written from AxisGroupManager.Update (per-state FB error checks) and
	   from the AxisGroupSM motion-FB supervisor. Cleared on UnInited entry.
	   Published in the SYS/GA_EV TCP reply as err_src / err_id so the UI
	   can show a cause instead of just "state=Error". *)
	LastErrorSource : STRING(40) := '';
	LastErrorID     : DWORD := 0;

	(* --- Bus-restart throttle.
	   UnInited entry pulses IoConfig_Globals.EtherCAT_Master_SoftMotion.xRestart
	   :=TRUE to re-sync slaves. An Error -> UnInited -> Error loop would hammer
	   the bus; LastBusRestartMs records the RuntimeMs of the last pulse so
	   UnInited entry can gate the restart to once per BUS_RESTART_COOLDOWN_MS.
	   0 = never restarted yet. *)
	LastBusRestartMs       : LINT := 0;
	BUS_RESTART_COOLDOWN_MS : LINT := 30000;

	(* --- Ring-buffer safety (ringbuf-bugs.md, Bundle 1).
	   Slots in minfo_buf / aux*_info_buf are 256 bytes: 1 length byte +
	   255 payload. Anything over MAX_SLOT_PAYLOAD would wrap via
	   DINT_TO_BYTE and silently corrupt the slot, so the TCP ingress drops
	   the packet and bumps OverlenDropCount instead.
	   ReMpDropCount counts reply-ring (reMP_info_ridx) drops: when the
	   ring is full, the oldest queued reply is consumed to make room.
	   Both counters are diagnostic only -- sustained growth means the
	   client is slower than the PLC can pack, or something is spamming
	   oversized packets. *)
	MAX_SLOT_PAYLOAD : DINT := 255;
	OverlenDropCount : UDINT := 0;
	ReMpDropCount    : UDINT := 0;

	(* Send-stall drop counter (ringbuf-bugs.md #7). Bumped when the
	   reply-ring head packet has been retried SEND_MAX_RETRIES cycles
	   while the socket still reports xActive=TRUE -- i.e. a half-open
	   or TX-backpressured peer that would otherwise block the ring. *)
	SendStallDropCount : UDINT := 0;

END_VAR
