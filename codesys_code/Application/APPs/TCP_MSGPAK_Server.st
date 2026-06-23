PROGRAM TCP_MSGPAK_Server
VAR
    // --- Server Configuration ---
    //
    // Bind to 0.0.0.0 (any interface). Pre-fix the server was bound to a
    // specific machine IP (192.168.1.70); when the PLC ran on a host whose
    // interfaces didn't include that IP, NBS silently failed to listen
    // while still reporting xServerActive=TRUE -- the symptom was raw
    // socket connect refused even though the FSM looked healthy and
    // UiPingCount/AxisGroupSMScans kept climbing. 0.0.0.0 makes the bind
    // succeed on whatever interface the host has, and the OS routes by
    // destination IP from the client side. If a specific-IP bind is ever
    // needed (e.g. firewall pinning), change the literal here -- it's
    // exposed as a GET_DIAG field too (see DrainHostPackets.st).
    //
    // Port: 8125, single-client TCP. See memory ui_plc_single_client_arbitration.
    bEnableServer         : BOOL := TRUE;
    ipServerAddress       : NBS.IPv4Address := (ipAddress:='0.0.0.0');
    uiServerPort          : UINT := 8125;

    // --- The Server Function Block Instance ---
    fbMyServer            : FB_TcpMsgPakServer;

    // --- Logic for Packet Flow Control ---
    REEN_CD               : INT := 0;

    recv_counter          : INT := 0;
    TMP_PKT_IDX           : UDINT := 0;

	tmp_MsgPakInfo_ptr:POINTER TO MsgPakInfo;
	tmp_ptr:POINTER TO BYTE;

	// Send-stall recovery: if Send() keeps returning FALSE while the
	// socket reports xActive=TRUE (half-open / TX backpressure), the
	// reply ring would fill up forever. Drop the head packet after
	// SEND_MAX_RETRIES consecutive cycles and count it. See
	// doc/3-subsystems/ringbuf-bugs.md #7.
	send_retry_count: UDINT := 0;
	// Comm task is 5ms; 20 cycles ~= 100ms before head-drop. Keeps the
	// ring from filling up behind one stuck packet during a half-open TX
	// stall (xActive=TRUE but Send returns FALSE). See ringbuf-bugs.md #7.
	SEND_MAX_RETRIES: UDINT := 20;

	// TX-side half-open escalation: count consecutive head-drops with no
	// successful Send between them. After the threshold, signal
	// FB_TcpMsgPakServer to force a socket reset so a fresh client can
	// reconnect. Reset to 0 on any successful Send. 5 head-drops at
	// ~100ms apart -> ~500ms of sustained TX stall before reclaiming
	// the socket; well under any human-perceivable hang and far above
	// transient TCP backpressure.
	consecutive_stall_drops: UDINT := 0;
	TX_STALL_DROP_THRESHOLD: UDINT := 5;
	tx_stall_force_reset: BOOL := FALSE;

	DBG_reMP_info_ridx_size:INT;
END_VAR
(* =========== IMPLEMENTATION =========== *)

//==================================================================================================
// PROGRAM IMPLEMENTATION
//==================================================================================================


IF REEN_CD>0 THEN
	REEN_CD:=REEN_CD-1;
END_IF


// --- 4. Call the server function block instance ---
// The PKT_ALLOW_NEXT input is now controlled by our logic.
fbMyServer(
    ENABLE:= bEnableServer,
    IP_ADDRESS:= ipServerAddress,
    PORT:= uiServerPort,
    PKT_ALLOW_NEXT:=GVL.minfo_buf_ridx.Space()>1,
    xForceReset:= tx_stall_force_reset
);
// One-shot pulse: clear after delivery so we don't loop-reset.
tx_stall_force_reset := FALSE;


// This IF statement will be TRUE for one cycle when a new packet arrives.
IF fbMyServer.PKT_IDX > TMP_PKT_IDX THEN
	REEN_CD:=50;
    recv_counter := recv_counter + 1;

	// Length guard: slots are 256 bytes (1 len + 255 payload). Over-length
	// packets would wrap via DINT_TO_BYTE and corrupt the slot silently.
	// See doc/3-subsystems/ringbuf-bugs.md #1. Type/cmd dispatch happens in AxisGroupSM
	// after the slot is drained; unrecognised types NAK with
	// 'missing_type_field'.
	IF (fbMyServer.BUFFER_LEN > GVL.MAX_SLOT_PAYLOAD) THEN
		GVL.OverlenDropCount := GVL.OverlenDropCount + 1;
	ELSIF (GVL.minfo_buf_ridx.Space() > 0) THEN
		tmp_ptr:=ADR(GVL.minfo_buf[GVL.minfo_buf_ridx.GetHead()][0]);
		tmp_ptr[0]:=DINT_TO_BYTE(fbMyServer.BUFFER_LEN);
		MEMUtils.MemCpy(ADR(tmp_ptr[1]),ADR(fbMyServer.BUFFER_PTR[0]),DINT_TO_UINT(fbMyServer.BUFFER_LEN));
		GVL.minfo_buf_ridx.PushHead();
	END_IF;
END_IF;

// Update the packet index tracker
TMP_PKT_IDX := fbMyServer.PKT_IDX;


DBG_reMP_info_ridx_size:=GVL.reMP_info_ridx.Size();
IF GVL.reMP_info_ridx.Size()>0 THEN
	IF fbMyServer.xResetting OR NOT fbMyServer.CLIENT.xActive THEN
		// Socket is dying (reset in flight) or already dead. Continuing
		// to Send would either succeed-then-lose-the-reply (client gone,
		// ConsumeTail already ran) or fail and pump the stall counter
		// for no reason. Flush the backlog so we don't replay stale
		// replies to whatever client connects next.
		WHILE GVL.reMP_info_ridx.Size() > 0 DO
			GVL.reMP_info_ridx.ConsumeTail();
		END_WHILE;
		send_retry_count := 0;
		consecutive_stall_drops := 0;
	ELSE
		tmp_MsgPakInfo_ptr:=ADR(GVL.reMP_info_arr[GVL.reMP_info_ridx.GetTail()]);
		// Only consume the slot if the TCP write succeeded. A transient
		// failure (xActive=TRUE, Send=FALSE) means TX-side backpressure;
		// leave the packet so it retries next cycle or escalates below.
		IF fbMyServer.Send(BUFFER_PTR := tmp_MsgPakInfo_ptr^.buf, BUFFER_LEN := tmp_MsgPakInfo_ptr^.buf_size) THEN
			GVL.reMP_info_ridx.ConsumeTail();
			send_retry_count := 0;
			// Successful Send proves the TX path is alive; clear the
			// half-open escalation counter.
			consecutive_stall_drops := 0;
		ELSE
			// xActive=TRUE but Send returned FALSE -> TX stall / half-open.
			// (Hard Write errors now raise xResetConnection inside Send.st,
			// so by next cycle xResetting will be TRUE and we'll hit the
			// flush branch above; the stall escalation here only fires on
			// pure backpressure where Write reported NO_ERROR but the
			// socket isn't draining.) See doc/3-subsystems/ringbuf-bugs.md #7.
			send_retry_count := send_retry_count + 1;
			IF send_retry_count >= SEND_MAX_RETRIES THEN
				GVL.reMP_info_ridx.ConsumeTail();
				GVL.SendStallDropCount := GVL.SendStallDropCount + 1;
				send_retry_count := 0;
				// TX-side half-open escalation. Each head-drop = ~100ms of
				// stall. Once we accumulate TX_STALL_DROP_THRESHOLD in a
				// row, the socket is genuinely dead from our side; raise
				// xForceReset so the FB tears down the connection and a
				// new client can reconnect.
				consecutive_stall_drops := consecutive_stall_drops + 1;
				IF consecutive_stall_drops >= TX_STALL_DROP_THRESHOLD THEN
					tx_stall_force_reset := TRUE;
					consecutive_stall_drops := 0;
				END_IF;
			END_IF;
		END_IF;
	END_IF;
END_IF

// --- MsgPack self-test harness hook (Comm task; runs cyclically).
// See doc/2-contracts/msgpack.md and FB_MsgPackTests. Toggle GVL.bRunMsgPackTests
// in online mode to fire; results on GVL.fbMsgPackTests.*.
GVL.fbMsgPackTests(bRun := GVL.bRunMsgPackTests);
