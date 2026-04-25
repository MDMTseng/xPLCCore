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

	(* Count of motion packets NAKed because the FSM wasn't Ready when the
	   packet was dequeued. Drives the host-authority NAK loop in the
	   CheckAxisGroupReady block of AxisGroupSM; see solidification.md W1 A1.
	   Diagnostic only -- sustained growth in production usually means the
	   UI is sending motion while the PLC is still powering/homing. *)
	GroupNotReadyNakCount : UDINT := 0;

	(* --- UI heartbeat (solidification.md W1 A3).
	   UI pings via SYS/PING every ~500ms; PLC stamps LastUiPingMs with the
	   current RuntimeMs and bumps UiPingCount. A supervisor can later compare
	   (RuntimeMs - LastUiPingMs) against a staleness threshold to e.g.
	   freeze motion if the UI has gone dark mid-cycle. Diagnostic only
	   until such a supervisor is wired. 0 = never pinged yet. *)
	LastUiPingMs : LINT := 0;
	UiPingCount  : UDINT := 0;

	(* Supervisor threshold: once the UI has pinged at least once, if no
	   further PING arrives within UI_HEARTBEAT_TIMEOUT_MS while the FSM is
	   Ready, the AxisGroupSM supervisor latches an error and drives the FSM
	   to Error (recoverable via EV_RESET -> UnInited). Zero would disable
	   the watchdog; keep > HEARTBEAT_STALE_MS in PluginHello.tsx (currently
	   3500ms) so the UI flags stale first, giving it a chance to reconnect
	   before the PLC halts motion. *)
	UI_HEARTBEAT_TIMEOUT_MS : LINT := 5000;
	UiHeartbeatStaleCount   : UDINT := 0;

	(* Post-homing coord-system gate.
	   Clear on UnInited entry, set by SetCoord0 / SetCoord1 handlers.
	   ProcessMotionPacket refuses G1 motion (NAK `err='coord_not_configured'`)
	   while this is FALSE. Prevents the "forgot to set coord1 after homing and
	   machine-coord G1 crashes into something" failure mode. Surfaced in the
	   SYS/GET_MACHINE_STATE reply so the UI can prompt the operator on
	   reconnect. *)
	CoordSystemConfigured : BOOL := FALSE;
	CoordNotConfiguredNakCount : UDINT := 0;

	(* Server-initiated event packets (push notifications).
	   AxisGroupSM emits an `ST_CHG` event into reMP_info_ridx on every
	   FSM state change (detected by PrevAxisGroupState diff). Packet shape:
	   `{kind:'event', name:'ST_CHG', st, st_str, from, runtime_ms}` with no
	   `id` field (unsolicited). StateChangeEventCount is the diagnostic
	   counter so we can verify the push loop is running without sniffing the
	   socket. Events share the reply-ring with request replies; if the client
	   is disconnected or backlogged, existing ring-overflow handling evicts
	   the oldest, matching the reply-drop policy. *)
	StateChangeEventCount : UDINT := 0;

	(* W1 A5 protocol version.
	   AxisGroupSM's packet dispatcher reads `protocol_version` off every
	   incoming packet. If present AND different from PROTOCOL_VERSION, the
	   packet is NAK'd with err='protocol_version_mismatch', err_got=<value>.
	   Absent field is treated as legacy (no version) and allowed through for
	   backward compatibility with scripted test helpers. Bump both this
	   constant and the UI-side PROTOCOL_VERSION in PluginHello.tsx together
	   whenever the wire format changes in a way old consumers can't handle.
	   ProtocolVersionMismatchCount is the diagnostic counter. *)
	PROTOCOL_VERSION : UDINT := 1;
	ProtocolVersionMismatchCount : UDINT := 0;

	(* TCP server stability counters (FB_TcpMsgPakServer).
	   IdleResetCount: connection forced to reset because no bytes arrived
	   for IDLE_TIMEOUT while CLIENT.xActive=TRUE -- catches half-open
	   sockets after host crash / cable yank where the peer never sends
	   FIN/RST. Without the reset, the single-connection slot stays
	   occupied and the next host can't reconnect.
	   ReadErrorResetCount, ParserErrorResetCount: existing reset triggers
	   that previously had no diagnostic surface. *)
	IdleResetCount         : UDINT := 0;
	ReadErrorResetCount    : UDINT := 0;
	ParserErrorResetCount  : UDINT := 0;

END_VAR
