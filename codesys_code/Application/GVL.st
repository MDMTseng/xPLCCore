{attribute 'qualified_only'}
VAR_GLOBAL
	
	
	(* Host->PLC packet rings, filled by TCP_MSGPAK_Server (Comm task),
	   drained by AxisGroupSM.DrainHostPackets (EC task).
	   minfo_buf  : motion packets and every SYS command whose order
	                relative to motion matters. FIFO; a motion packet the
	                PLC cannot take yet (buffer full, BLOCK_ waits) holds it.
	   sysinfo_buf: order-free SYS commands (see TCP_MSGPAK_Server
	                IsOrderFreeSys), drained completely every scan so
	                PING, GA_EV and state reads never wait behind motion
	                (review 2026-09-24 P0-3). *)
	minfo_buf: ARRAY [0..15] OF ARRAY [0..256-1]OF BYTE;
	minfo_buf_ridx:FB_RingBufferIndex(16);
	sysinfo_buf: ARRAY [0..7] OF ARRAY [0..256-1]OF BYTE;
	sysinfo_buf_ridx:FB_RingBufferIndex(8);


	reMP_info_SIZE:INT:=32;
	// Reply-buffer slot size. Bumped from 256 to 1024 on 2026-04-27 after
	// observing slot[6].buf_size=304 in a live snapshot (GET_MACHINE_STATE
	// reply overflowed). At 256 a >256-byte reply silently overran into the
	// adjacent reMP_info_arr struct memory (no bounds check in Pack* path),
	// corrupting later slots' buf pointers and crashing PLC in
	// FB_MpPacker.PackMapHeader when ridx wrapped to a corrupted slot.
	reMP_info_buff_SIZE:INT:=1024;
	// Capacity bumped 32 -> 128 on 2026-04-28. With HEARTBEAT (~2/s) + PING
	// replies + ST_CHG bursts, a 5s UI hiccup could fill 32 slots. 128 gives
	// headroom for the A3 supervisor staleness window (5s) plus margin.
	// Memory cost: 128 * 1024 = 128KB for buf, ~negligible struct overhead.
	reMP_info_buf: ARRAY [0..128-1] OF ARRAY [0..1024-1]OF BYTE;
	// Guard region between the byte buffers and the struct array. If any
	// future reply overflows past buf_capacity, it lands here (harmless
	// scratch) rather than smashing reMP_info_arr[0]'s buf pointer field.
	// Sized at 1KB -- larger than any plausible single-Pack* overrun.
	reMP_info_buf_guard: ARRAY [0..1023] OF BYTE;
	reMP_info_arr: ARRAY [0..128-1] OF MsgPakInfo;
	reMP_info_ridx:FB_RingBufferIndex(128);

	// Scratch slot used by long emission blocks (ProcessMotionPacket,
	// AxisGroupSM SYS handlers) when the ring is full. Drop-newest policy
	// (see UpdateMotionProgress comment) means the producer never writes
	// to slot[head] when full -- writing there would corrupt the slot the
	// Comm task is about to consume. Routing the pack into this scratch
	// slot keeps the inline pack code unchanged; the gate on PushHead
	// then quietly drops the packet and bumps ReMpDropCount.
	reMP_info_scratch: MsgPakInfo;
	// Same dedicated buffer for the scratch slot. Sized like reMP_info_buf
	// rows; never sent on the wire, just absorbs writes when the ring is
	// full.
	reMP_info_scratch_buf: ARRAY [0..1024-1] OF BYTE;

	// Bumped by MsgPakInfoWrapup when whead has already passed buf_capacity --
	// i.e. some Pack* call wrote past the slot's byte buffer. Diagnostic only;
	// the corruption already happened upstream, but a non-zero count means
	// the buf size needs raising or a reply path is unbounded.
	ReMpOverflowDropCount : UDINT := 0;


	(* --- MsgPack self-test harness (bypasses web UI).
	   Toggle bRunMsgPackTests in online mode; read outputs on the FB
	   instance. See doc/2-contracts/msgpack.md for expected failures on today's
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

	(* --- Per-axis simulation mask (bench mode, 2026-07-08).
	   Lets the host run the reel-pull motor REAL while the delta trio is
	   simulated (tape-binding bench), without forking the FSM/dispatch
	   semantics: the FSM climbs to Ready exactly as usual, only the
	   drives' virtual mode differs. Runtime switch is SMC3_ReinitDrive
	   with bVirtual per axis (SM3_Basic; equivalent to the SoftMotion
	   Device Editor "virtual mode" checkbox, no device-tree edit needed).

	   Bit layout matches axes_labels order (bit0..2 = EAxis0/1/2 delta
	   trio, bit3 = reelpullmotor; 1 = simulated).

	   AxisSimMask_Request : written by the SYS SET_AXIS_SIM handler
	     (UnInited only). Self-expires AXIS_SIM_TTL_MS after the last
	     accepted write -- deliberately a RuntimeMs deadline instead of a
	     TON: the legacy TON gate latches Q while IN stays TRUE (see
	     memory virtual_motors_gate_ton_reset), a re-stamped deadline
	     avoids that trap. Expiry is evaluated in UnInited, so an applied
	     mask is never yanked out from under a powered session; the NEXT
	     reset after expiry reverts to AxisSimConfigMask.
	   AxisSimConfigMask   : the device-tree "virtual mode" of each axis,
	     captured once on the first scan (before any reinit). Always OR'd
	     into the desired mask: SET_AXIS_SIM can only ADD simulation, and
	     neither a smaller request nor TTL expiry can turn an axis that
	     is virtual in the project into a real drive. Making such an axis
	     real takes a project change and download
	     (jobs/templates/set_axis_virtual.py). Safety: on this rig the
	     delta trio is virtual in the project and must never move.
	   AxisSimMaskApplied  : what the drives actually run right now.
	     LIVE-DERIVED each scan from the axes' own bVirtual flags (the
	     drives are the source of truth -- this also captures the
	     device-tree "virtual mode" checkbox at boot, which on this rig
	     has the delta trio virtual).
	   AxisSimMask         : the PUBLISHED effective mask (Applied OR
	     16#0F while the legacy bVirtualMotorsMode gate is open). This is
	     what GET_MACHINE_STATE reports as axes_sim_mask and what the
	     HOME_GO_FORCE_SKIP gate checks.
	   AxisSimHostRequested: latched TRUE by the first accepted
	     SET_AXIS_SIM. The reinit sequence only ever runs after the host
	     opted in -- the legacy virtual_motors_force.py flow alone must
	     keep today's behavior bit-identical (it never touched drives). *)
	AxisSimMask_Request  : BYTE := 0;
	AxisSimMaskApplied   : BYTE := 0;
	AxisSimMask          : BYTE := 0;
	AxisSimHostRequested : BOOL := FALSE;
	AxisSimConfigMask    : BYTE := 0;
	AxisSimConfigCaptured : BOOL := FALSE;
	AxisSimMaskSetMs     : LINT := 0;
	AXIS_SIM_TTL_MS      : LINT := 600000;

	(* --- PC simulation without a fieldbus (CODESYS Control Win, see
	   memory local-softplc-sim). There the EtherCAT master can never
	   open a network adapter, so its xError would keep the FSM in
	   Error. SimNoFieldbus lets the supervisor ignore that, but ONLY
	   while every axis (delta trio, EAXIS_A, SM_Drive_GenericDSP402,
	   reel) is virtual. On the machine EAXIS_A is a real drive, so
	   setting this there changes nothing. Not retained: every download
	   clears it; the sim harness writes it after install. *)
	SimNoFieldbus        : BOOL := FALSE;

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
	   Slots in minfo_buf are 256 bytes: 1 length byte + 255 payload.
	   Anything over MAX_SLOT_PAYLOAD would wrap via DINT_TO_BYTE and
	   silently corrupt the slot, so the TCP ingress drops the packet and
	   bumps OverlenDropCount instead.
	   ReMpDropCount counts reply-ring (reMP_info_ridx) drops: when the
	   ring is full, the oldest queued reply is consumed to make room.
	   Both counters are diagnostic only -- sustained growth means the
	   client is slower than the PLC can pack, or something is spamming
	   oversized packets. *)
	MAX_SLOT_PAYLOAD : DINT := 255;
	OverlenDropCount : UDINT := 0;
	(* Overlength packet NAK hand-off: the Comm task records the dropped
	   packet's id here and the EC task sends the NAK (it is the only
	   producer of the reply ring). One pending id; a second overlength
	   packet before it is sent only bumps OverlenDropCount. *)
	OverlenNakId      : DINT := 0;
	(* Host packets taken in, and replies sent, per Comm scan. It was one of
	   each: two back-to-back G1s took two scans to even reach the ring.
	   1 = the old behaviour (A/B switch, online-writable). *)
	CommPacketsPerScan : INT := 4;
	(* G1s the axis group did not accept at once (CommandAccepted FALSE):
	   each costs a RETRY_COOLDOWN_G1_MS wait before the retry. *)
	G1RetryCount : UDINT := 0;

	(* Servo peak monitor: largest |set velocity|, |set acceleration| and
	   |jerk| (d acc / dt, per EC scan) of each axis since the last reset,
	   in axis units (joints: deg, deg/s, deg/s^2, deg/s^3 at the joint;
	   reel: its units). Index 0-2 EAxis0-2, 3 SM_Drive_GenericDSP402 (the
	   group's A axis), 4 EAXIS_A, 5 reelpullmotor. Set MotionPeakReset to
	   clear. Read by tools/sim/run_virtual.py (--peaks). *)
	(* Speed override (SYS SET_OVERRIDE, the UI's speed slider): time-scales
	   all motion -- velocity x f, acceleration x f^2, jerk x f^3 -- so
	   paths keep their shape and just run slower, including the moves
	   already running. 0.01..1; not retained (a restart runs at 100 %). *)
	OverrideFactor : LREAL := 1.0;

	MotionPeakReset : BOOL := FALSE;
	MotionPeakVel   : ARRAY[0..5] OF LREAL;
	MotionPeakAcc   : ARRAY[0..5] OF LREAL;
	MotionPeakJerk  : ARRAY[0..5] OF LREAL;
	OverlenNakPending : BOOL := FALSE;
	ReMpDropCount    : UDINT := 0;

	(* Per-type drop breakdown (added 2026-04-28). With seq stamping, the UI
	   can already detect *that* a packet was dropped; these counters tell
	   it *what kind* without having to keep a seq->kind history. The five
	   categories cover every reMP_info_ridx producer site:
	     _Reply        : request acks/naks (SYS, M, motion-packet)
	     _StChg        : FSM state-change push event
	     _CoordSet     : coord-system gate edge push event
	     _Heartbeat    : periodic PLC->UI heartbeat
	     _TriggerEvent : fly-event trigger error code push
	   Sum of these five may slightly exceed ReMpDropCount under unusual
	   races (drop-bump runs in producer task, global counter is shared);
	   that's diagnostic-only so the slop is acceptable. *)
	ReMpDropCount_Reply        : UDINT := 0;
	ReMpDropCount_StChg        : UDINT := 0;
	ReMpDropCount_CoordSet     : UDINT := 0;
	ReMpDropCount_Heartbeat    : UDINT := 0;
	ReMpDropCount_TriggerEvent : UDINT := 0;

	(* Global monotonic sequence stamped on every packet emitted via
	   MsgPakInfoWrapup (replies + unsolicited events alike). UI uses this
	   to detect ring-drop gaps that per-request id-tracking can't see --
	   e.g. a dropped ST_CHG/MOVE_DONE event has no `id` to time-out on, but
	   leaves a hole in the seq stream. Wraps at 2^64-1; even at sustained
	   1kHz emission that's ~585M years, so wrap-detection is unnecessary. *)
	ReMpSeqCounter : ULINT := 0;

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

	(* Count of packets NAKed because they had no recognised type tag (not
	   'SYS', not 'M', not protocol-mismatched). Pre-fix these silently sat
	   at the ring tail until a state transition drained them, blocking any
	   SYS packets queued behind. Sustained growth means a UI sender forgot
	   type:'M' on a motion command (closed B1 ghost). Diagnostic only. *)
	MissingTypeFieldNakCount : UDINT := 0;

	(* --- UI heartbeat (solidification.md W1 A3).
	   UI pings via SYS/PING every ~500ms; PLC stamps LastUiPingMs with the
	   current RuntimeMs and bumps UiPingCount. A supervisor can later compare
	   (RuntimeMs - LastUiPingMs) against a staleness threshold to e.g.
	   freeze motion if the UI has gone dark mid-cycle. Diagnostic only
	   until such a supervisor is wired. 0 = never pinged yet. *)
	LastUiPingMs : LINT := 0;
	(* Packets received from the host, bumped by TCP_MSGPAK_Server for
	   every packet (dropped ones included). AxisGroupSM refreshes
	   LastUiPingMs when it changes. *)
	HostRxCount : UDINT := 0;

	(* Event timestamp log (1 ms), for measuring the timing-critical
	   paths (plc.md). Written only from EtherCAT_Task: PRG_EventLog
	   (output/input edges, motion segments, reel busy, FSM state) and
	   AxisGroupSM.DrainHostPackets (host marks, SYS EVT_MARK). Event n
	   lives in slot n MOD 1024; EvHead = events written so far. Read by
	   PRG_EcatEspHttp at GET /e?s=<first seq>. Kinds:
	     1 output on (val = bit)      2 output off
	     3 input on  (val = bit)      4 input off
	     5 motion segment start (val = movement id)   6 group idle
	     7 reel move start             8 reel move end  (val = reel position, 0.01 units, DINT bits)
	     9 FSM state (val = E_RobotState)
	    10 host mark (val = code, see tools/sim/event_log.py)
	    11/12/13 arm X/Y/Z, 0.01 mm, DINT bit pattern (every 50 ms
	             when the arm moved > 0.05 mm) *)
	EvHead : UDINT := 0;
	EvT    : ARRAY[0..1023] OF UDINT;   // AxisGroupSM.RuntimeMs, low 32 bits
	EvKind : ARRAY[0..1023] OF BYTE;
	EvVal  : ARRAY[0..1023] OF UDINT;
	UiPingCount  : UDINT := 0;

	(* Worst-case observed gap between consecutive PINGs (ms, PLC clock).
	   Generic comm-stability metric: lets the UI dashboard "is the
	   heartbeat steady or jittery?" without inferring from PLC-RTT alone.
	   Updated in the PING handler; resettable via SYS/RESET_DBG_INFO.
	   First PING after reset/boot does not contribute (no prior to delta
	   against). 0 = no two PINGs seen yet since reset. *)
	PingMaxGapMs       : UDINT := 0;
	PrevPingRuntimeMs  : LINT  := 0;

	(* Supervisor threshold: once the UI has pinged at least once, if no
	   further PING arrives within UI_HEARTBEAT_TIMEOUT_MS while the FSM is
	   Ready, the AxisGroupSM supervisor latches an error and drives the FSM
	   to Error (recoverable via EV_RESET -> UnInited). Zero would disable
	   the watchdog; keep > HEARTBEAT_STALE_MS in PluginHello.tsx (currently
	   3500ms) so the UI flags stale first, giving it a chance to reconnect
	   before the PLC halts motion. Tightened 2026-04-28 from 10000 -> 5000
	   so PLC halts ~1.5s after the UI flags stale, instead of 6.5s. *)
	UI_HEARTBEAT_TIMEOUT_MS : LINT := 5000;
	UiHeartbeatStaleCount   : UDINT := 0;

	(* SoftMotion kinematic-group ErrorStop supervisor trips. Bumps each
	   time the AxisGroupSM supervisor sees GroupReadStatusFb.GroupErrorStop
	   rise while the FSM was in any non-Error/UnInited state. Useful to
	   distinguish "FSM went to Error from a healthy state" (real fault)
	   from "FSM was already in Error" (just stuck). Common cause is the
	   well-known virtual-axis instant-complete fall-through; on real
	   motors a non-zero value means a servo/bus fault tripped MC_GroupStop. *)
	GroupErrorStopTripCount : UDINT := 0;

	(* Post-homing coord-system gate.
	   Clear on UnInited entry, set by SetCoord0 / SetCoord1 handlers.
	   ProcessMotionPacket refuses G1 motion (NAK `err='coord_not_configured'`)
	   while this is FALSE. Prevents the "forgot to set coord1 after homing and
	   machine-coord G1 crashes into something" failure mode. Surfaced in the
	   SYS/GET_MACHINE_STATE reply so the UI can prompt the operator on
	   reconnect. *)
	CoordSystemConfigured : BOOL := FALSE;
	CoordNotConfiguredNakCount : UDINT := 0;

	(* SCRATCHPAD_WRITE partial-write rejection. The §4(2) wire contract
	   requires the host to send ALL 5 fields per write (plan_id,
	   plan_index, intent_kind, intent_movement_id, last_vision_pulse).
	   Pre-fix, TryReadINT64 returned default 0 on a missing key so a host
	   bug that omitted a field silently zero-stomped that slot AND ACKed
	   success. Post-fix, the handler reads with -1 sentinel and NAKs the
	   whole write if any field is missing; this counter bumps each NAK. *)
	ScratchpadPartialNakCount : UDINT := 0;

	(* SetCoord0/1 motion-in-flight rejection. SetCoord0/1 mutates the
	   active MC_COORD_REF transform (CoordTransform) and pulses
	   MC_SetCoordinateTransform's Execute. If a G1 is mid-flight the WCS
	   origin shifts under it and the arm darts. Mirrors COORD1_BIND's
	   own MotionBufferSize>0 guard. *)
	SetCoordBusyNakCount : UDINT := 0;

	(* GA_EV unknown-event rejection. Pre-fix, TryReadINT64('ev', -1)
	   followed by TO_UDINT(-1) wrapped to 16#FFFFFFFF and was passed to
	   Transition; no CASE matched so the FSM no-op'd but the reply still
	   carried CommandAck=TRUE, fooling the host into thinking its event
	   landed. NAK these instead so a malformed GA_EV is observable. *)
	UnknownEventNakCount : UDINT := 0;

	(* R2/R3/R4/R5/R11 fixes (test_plc_refactor_findings): every rejected
	   packet must NAK-or-count. UnknownCmdNakCount = M packet whose cmd
	   matched no dispatcher branch (used to NAK bare, no err/counter).
	   FlyEventRejectNakCount = M4 / WAIT_FOR_TRIGGER registration-gate
	   refusal (used to be consumed silently). FlyEventBufferFullNakCount =
	   M4 / WAIT_FOR_TRIGGER arriving with <=3 free FlyEvent slots (used to
	   sit unconsumed at the minfo tail and wedge the whole inbound queue).
	   All three should stay 0 in production; nonzero means a host bug or
	   slot-leak pressure. *)
	UnknownCmdNakCount         : UDINT := 0;
	FlyEventRejectNakCount     : UDINT := 0;
	FlyEventBufferFullNakCount : UDINT := 0;

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

	(* PH#6: G1 dedupe counter. Bumps every time a G1 with a CommandId
	   matching a recently-accepted G1 was caught and replayed instead of
	   re-executed. Non-zero on a stable run usually means UI-side TCP
	   retry firing on jitter (benign, the dedupe did its job) or a
	   collapsed button bounce. Compare against G1 ack count to spot
	   anomalies. *)
	DupeCommandCount : UDINT := 0;

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
	(* TX Write-error reset: NBS.CLIENT.Write returned non-NO_ERROR.
	   Pre-fix the error was swallowed -- caller saw Send=FALSE and went
	   down the TX_STALL_DROP_THRESHOLD escalation path, costing ~500ms
	   before the socket got reclaimed. Post-fix Send.st now raises
	   xResetConnection immediately, symmetric with the Read-error path. *)
	WriteErrorResetCount   : UDINT := 0;
	(* TCP listen-health diagnostics.
	   ClientConnectCount: bumped on every rising edge of CLIENT.xActive --
	   answers "is the server actually accepting connections, or has the
	   bind silently failed?". Pre-existing UiPingCount only counted PING
	   packets, so a config-drift scenario where the bind was wrong looked
	   identical to "client just hasn't pinged yet" (both 0).
	   ServerLongIdleCount: bumped once per "active-but-no-client window
	   of NO_CLIENT_TIMEOUT". Fires repeatedly only if a client briefly
	   connects then drops between idle windows. A sustained value of 0
	   while UiPingCount=0 means nobody's tried to connect; a growing
	   ServerLongIdleCount while UiPingCount=0 means the bind is up but
	   no one's reaching it (firewall, wrong IP, etc). *)
	ClientConnectCount     : UDINT := 0;
	ServerLongIdleCount    : UDINT := 0;
	(* TX-side half-open detection: SendStallDropCount counts head-drops
	   when xActive=TRUE but Send keeps failing. Pre-fix the ring would
	   keep churning forever -- one head-drop every ~100ms -- and never
	   reclaim the socket slot. TxStallResetCount counts how many times
	   sustained stall (TX_STALL_DROP_THRESHOLD consecutive head-drops
	   with no successful Send between) escalated to a forced socket
	   reset. *)
	TxStallResetCount      : UDINT := 0;
	(* Audit-fix: same-state EV_ERROR/EV_RESET re-entries. Pre-fix these
	   were swallowed (new_state == _eState so the entry actions didn't
	   run again) and stacked faults masked each other. Counter climbs
	   each time Transition.st re-pulses Update(1)/Update(-1) on the
	   same state. Expected to grow only under repeated fault triggers. *)
	SelfReentryCount       : UDINT := 0;
	(* Audit-fix: deferred-event retry pumps (PendingMoveDoneId,
	   PendingStChgFromState) couldn't escape a permanently-full reMP
	   ring. After PENDING_RETRY_MAX_SCANS scans the pump now force-drops
	   the pending event and bumps these counters. Non-zero means the UI
	   missed an event entirely (vs the normal HEARTBEAT-bounded skip). *)
	PendingMoveDoneDropCount : UDINT := 0;
	PendingStChgDropCount    : UDINT := 0;

	(* B1 diagnostic counters for SetCoord1 stall investigation.
	   AxisGroupSMScans bumps once per scan at the very top of AxisGroupSM
	   so we can detect a cyclic-task stall by observing it stop advancing.
	   The DBG_SetCoord* fields latch RuntimeMs at each step of the
	   SetCoord1 dispatch -> SetCoordTransformFb lifecycle so we can
	   measure how long Busy is held without sub-cycle online sampling. *)
	AxisGroupSMScans              : UDINT := 0;
	DBG_SetCoord1AcceptMs         : LINT := 0;
	DBG_SetCoordExecuteRiseMs     : LINT := 0;
	DBG_SetCoordBusyFirstMs       : LINT := 0;
	DBG_SetCoordDoneMs            : LINT := 0;
	DBG_SetCoordErrorMs           : LINT := 0;
	DBG_SetCoordLastErrorID       : DWORD := 0;
	DBG_SetCoordExecuteFallMs     : LINT := 0;

	(* Per-axis DS402 drive-interface error latch. Cyclic-side reader in
	   AxisGroupSM copies `<axis>.uiDriveInterfaceError` once per scan into
	   this array so the pure-read GET_MACHINE_STATE handler can return
	   per-axis diagnostic codes without touching the axis FBs.

	   Field choice (probed live 2026-04-27 via probe_axis_errorident*.py):
	   ErrorIdent.* (the SM3 generic struct) does not resolve on these
	   SM_Drive_GenericDSP402 axes; uiDriveInterfaceError is the DS402
	   drive-interface error mirror and is the only real diagnostic-code
	   field that exists on this drive type. wCommunicationState also
	   resolves but is a state code, not a fault code.

	   Index layout matches the bit layout of axes_err_mask:
	     [0] = IoConfig_Globals.EAxis0
	     [1] = IoConfig_Globals.EAxis1
	     [2] = IoConfig_Globals.EAxis2
	     [3] = reelpullmotor
	   0 = no fault. *)
	LastAxesErrorIDs : ARRAY[0..3] OF UINT;

	(* --- Conveyor tracking / coord1 bind state (Phase 1 scaffold).
	   ConveyorPulseRaw is the per-scan-fresh DINT pulse count from the
	   conveyor encoder. Phase 1 leaves it as a plain forceable variable
	   (no EC slave yet); once the encoder slave is added to the hardware
	   tree, map the PDO directly onto this symbol so the assignment
	   here becomes a no-op alias.

	   COORD1_BIND SYS handler writes Coord1RefPulse / Coord1RefXyz /
	   Coord1Scale and sets Coord1Bound. While Coord1Bound, the cyclic
	   path in AxisGroupSM recomputes Coord1OriginNow each scan as:
	     OriginNow[i] = RefXyz[i] + (Pulse - RefPulse) / Scale[i]
	   With Scale[i]=0 the axis is treated as locked (origin stays at
	   RefXyz[i]); only the belt-direction axis needs a non-zero scale.

	   G1 with frame="coord1" adds OriginNow to the raw target before
	   handing to MoveLinearAbsolute. coord0 path is unaffected. *)
	ConveyorPulseRaw     : DINT := 0;

	(* Synthetic pulse generator. Used before the real EC encoder slave
	   exists, so Phase 4 (MC_TrackConveyorBelt) can be exercised without
	   hardware. When ConveyorPulseSyntheticEnable=TRUE, the per-scan
	   compute in AxisGroupSM monotonically advances ConveyorPulseRaw
	   by Step (DINT accumulation, no wrap -- 2.1e9 pulses of headroom
	   covers a single test run). Default OFF so existing Phase 1/2
	   force-pulse probes still see a stable value when they unforce.
	   Step is the per-EC-scan delta; with 1ms EC tick a Step of 1
	   gives 1000 pulses/s. Toggle via daemon:
	     oapp.set_prepared_value('GVL.ConveyorPulseSyntheticEnable','TRUE')
	     oapp.force_prepared_values() *)
	ConveyorPulseSyntheticEnable : BOOL := FALSE;
	ConveyorPulseSyntheticStep   : DINT := 1;

	Coord1Bound          : BOOL := FALSE;
	Coord1RefPulse       : DINT := 0;
	Coord1RefXyz         : ARRAY [0..2] OF LREAL := [0.0, 0.0, 0.0];
	Coord1Scale          : ARRAY [0..2] OF LREAL := [0.0, 0.0, 0.0];
	Coord1OriginNow      : ARRAY [0..2] OF LREAL := [0.0, 0.0, 0.0];
	Coord1BindCount      : UDINT := 0;
	Coord1BindBusyNakCount : UDINT := 0;

	(* --- Coord1 cam-table mode (Phase 2).
	   Linear mode (default) uses Coord1Scale; Table mode stores a master->
	   slave-offset table sampled at COORD1_CAM_MAX_POINTS=64 points and
	   linearly interpolates between them. master[] must be strictly
	   ascending (binary-search lookup assumes monotonic). Out-of-range
	   pulse clamps to the first or last sample (no extrapolation).

	   COORD1_BIND chooses mode by payload shape:
	     - cam_table absent / count=0 -> Linear (uses Scale)
	     - cam_table.master[] present -> Table (Scale ignored)
	   Both supplied -> NAK 'coord_bind_conflict' (host bug, refuse).

	   Memory cost: 64 * (4 + 4*3) = 1KB. Trivial vs 128 reMP slots.
	   Bump COORD1_CAM_MAX_POINTS if cam profile granularity demands it
	   (also bump the input slot buf size if msgpack array overflows). *)
	(* Mode tag: 0=Linear, 1=Table. Plain INT (vs enum DUT) so the
	   field is editable without creating a new project object via
	   IDE/script; trivial CASE labels read just as well. Update
	   COORD1_MODE_* below in lockstep. *)
	COORD1_MODE_LINEAR         : INT := 0;
	COORD1_MODE_TABLE          : INT := 1;
	Coord1CamMode              : INT := 0;
	Coord1CamCount             : INT := 0;
	Coord1CamMaster            : ARRAY [0..63] OF DINT;
	Coord1CamX                 : ARRAY [0..63] OF REAL;
	Coord1CamY                 : ARRAY [0..63] OF REAL;
	Coord1CamZ                 : ARRAY [0..63] OF REAL;
	Coord1ConflictNakCount     : UDINT := 0;
	Coord1BadTableNakCount     : UDINT := 0;
	Coord1NotBoundNakCount     : UDINT := 0;
	Coord1BadFrameNakCount     : UDINT := 0;
	COORD1_CAM_MAX_POINTS      : INT := 64;

	(* Phase 4 step 3: tracking window + error reporting.
	   Coord1ExitPulse: when ConveyorPulseRaw exceeds this, the active
	   tracking is forcibly torn down and an EV_ERROR is fired so the
	   host can't silently miss a pick that ran out of belt budget.
	   Set by COORD1_BIND from ref_pulse + exit_pulse_offset.
	   Sentinel value -1 means "no window check" (used by the SYS BIND
	   path which is dev/test only -- production goes via FlyEvent BIND
	   which always carries an exit_pulse_offset).
	   Coord1BindEventId: host's correlation id, echoed in COORD1_ERROR. *)
	Coord1ExitPulse            : DINT := -1;
	Coord1BindEventId          : DINT := 0;
	(* Snapshot taken at the instant the window-exit fault triggers, so
	   the host can classify the failure (silent-quit vs ran-out-of-budget
	   vs stuck) without having to keep polling these fields itself. *)
	Coord1ErrPending           : BOOL := FALSE;
	Coord1ErrAtMovementId      : ULINT := 0;
	Coord1ErrAtProgress        : REAL := 0.0;
	Coord1ErrAtPulse           : DINT := 0;
	Coord1ErrAtArmX            : LREAL := 0.0;
	Coord1ErrAtArmY            : LREAL := 0.0;
	Coord1ErrAtArmZ            : LREAL := 0.0;
	Coord1ErrAtPcsX            : LREAL := 0.0;
	Coord1ErrAtPcsY            : LREAL := 0.0;
	Coord1ErrAtPcsZ            : LREAL := 0.0;
	Coord1ErrRefPulse          : DINT := 0;
	Coord1ErrExitPulse         : DINT := 0;
	Coord1ErrCount             : UDINT := 0;
	Coord1FlyBindCount         : UDINT := 0;
	Coord1FlyBindActiveCount   : UDINT := 0;

	(* Build provenance stamped by codesys_scripts/jobs/stamp_build_info.py
	   right before push (import_all). Lets an external tool answer "is the
	   running PLC the same commit as the repo?" without IDE assistance.
	   SHA: short git rev (up to 12 chars), 'dirty' suffix if working tree
	   was modified at stamp time. TS: unix milliseconds at stamp. Defaults
	   are 'unknown'/0 so an unstamped build is obvious instead of silently
	   misreporting. *)
	BUILD_GIT_SHA : STRING(40) := 'unknown';
	BUILD_TS_MS   : LINT       := 0;

	(* All-virtual scene: when TRUE, AxisGroupSM reads its digital inputs
	   from SimDigitalInput instead of the coupler (InputCHs[1..2]). Byte 0 =
	   inputs 0-7, byte 1 = inputs 8-15. 16#0C in byte 1 is "all normal" for
	   the renderer's input watchdog (CalibPage.tsx IO_Pins.I):
	   bit 11 PackedReelNoProtrusion = 1, bit 10 ReelPressRollerInPlace = 1,
	   bit 9 ReelTapeHTension = 0, bit 8 ReelLacking = 0.
	   Not retained: a download or restart returns to the real inputs. *)
	SimDigitalInputEnable : BOOL := FALSE;
	(* Simulated home switches for FB_Homing, virtual axes only: the switch
	   of EAxisN reads "hit" while its position is >= SimHomeSwitchPos
	   (see FB_Homing for SMC_Homing's polarity).
	   Lets the full homing sequence run in the all-virtual scene. A real
	   axis always uses its IOHUB_motN_limit input. Not retained. *)
	SimHomeSwitchEnable   : BOOL := FALSE;
	SimHomeSwitchPos      : LREAL := 10.0;
	SimDigitalInput       : ARRAY[0..1] OF USINT := [16#00, 16#0C];

END_VAR

VAR_GLOBAL RETAIN
	(* Host-owned resume scratchpad. See APP_COMM_FBs/Scratchpad_v1.
	   PLC never interprets fields other than SchemaVersion and
	   BootEpoch. SCRATCHPAD_WRITE SYS handler stores host bytes;
	   GET_MACHINE_STATE includes a nested `scratchpad` map on read.
	   Decisions: doc_review/decisions_2026-06-22.md §4 (2). *)
	ScratchpadV1 : Scratchpad_v1;

	(* Increments on every cold/warm restart. AxisGroupSM startup path
	   bumps this once per app boot (gated by AppBootHandled). Renderer
	   captures into ScratchpadV1.BootEpoch on write; mismatch on
	   re-read means PLC restarted since the write and the cursor is
	   stale. Cold reset wipes both to 0 -- renderer treats
	   SchemaVersion=0 as "invalid, cold-start". *)
	BootEpochCount : UDINT;

	(* Production plan, kept by the PLC so a renderer crash or PC restart
	   can resume where the tape actually is (owner, 2026-09-25).
	   The PLC only remembers the whole plan and how many tape cells have
	   been advanced since it was set; where that puts the job (which
	   segment, how much of it is left) is derived by the renderer
	   (lib/production/plan.ts remainingPlan). Segment n > 0 packs n
	   cells, n < 0 leaves |n| cells empty; either way the tape moves |n|.
	   PlanCellsDone grows only when a TAPE_CYCLE carrying `cells` has
	   actually moved the reel, so it can never run ahead of the tape.
	   The renderer also says what the cells are (`kind`: 1 packed, 2 left
	   empty); the PLC counts them apart and checks the kind and size
	   against the segment the plan puts at PlanCellsDone. A disagreement
	   bumps PlanMismatchCount and the TAPE_CYCLE reply carries plan_err
	   (the tape has moved either way: the count stays true to the tape).
	   PlanRev bumps on every change. *)
	PlanSeg       : ARRAY[0..31] OF DINT;
	PlanSegCount  : INT;
	PlanId        : DINT;
	PlanRev       : UDINT;
	PlanCellsDone : UDINT;
	PlanCellsPacked   : UDINT;
	PlanCellsEmpty    : UDINT;
	PlanMismatchCount : UDINT;
END_VAR

VAR_GLOBAL
	(* One-shot flag for boot-epoch increment. NOT retained -- a fresh
	   FALSE on every cold start, set TRUE in AxisGroupSM after the
	   first scan does the bump. *)
	AppBootHandled : BOOL := FALSE;
END_VAR
