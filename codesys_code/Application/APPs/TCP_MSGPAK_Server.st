PROGRAM TCP_MSGPAK_Server
VAR
    // --- Server Configuration ---
    bEnableServer         : BOOL := TRUE;
    ipServerAddress       : NBS.IPv4Address := (ipAddress:='192.168.1.70');
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
	tmp_MsgPakInfo_ptr:=ADR(GVL.reMP_info_arr[GVL.reMP_info_ridx.GetTail()]);
	// Only consume the slot if the TCP write succeeded. A failure here
	// means the client went away between queueing and this cycle's write;
	// leave the packet in the ring so it can retry next cycle once the
	// connection re-establishes, or be flushed by the reset path if not.
	IF fbMyServer.Send(BUFFER_PTR := tmp_MsgPakInfo_ptr^.buf, BUFFER_LEN := tmp_MsgPakInfo_ptr^.buf_size) THEN
		GVL.reMP_info_ridx.ConsumeTail();
		send_retry_count := 0;
		// Successful Send proves the TX path is alive; clear the
		// half-open escalation counter.
		consecutive_stall_drops := 0;
	ELSIF NOT fbMyServer.CLIENT.xActive THEN
		// Connection is down -- retrying forever is pointless. Drop the
		// backlog so we don't replay stale replies to a future client.
		WHILE GVL.reMP_info_ridx.Size() > 0 DO
			GVL.reMP_info_ridx.ConsumeTail();
		END_WHILE;
		send_retry_count := 0;
		consecutive_stall_drops := 0;
	ELSE
		// xActive=TRUE but Send keeps failing -> TX stall / half-open.
		// Drop the head packet after SEND_MAX_RETRIES cycles so the ring
		// doesn't fill up behind a stuck packet. See doc/3-subsystems/ringbuf-bugs.md #7.
		send_retry_count := send_retry_count + 1;
		IF send_retry_count >= SEND_MAX_RETRIES THEN
			GVL.reMP_info_ridx.ConsumeTail();
			GVL.SendStallDropCount := GVL.SendStallDropCount + 1;
			send_retry_count := 0;
			// TX-side half-open escalation. Each head-drop = ~100ms of
			// stall. Once we accumulate TX_STALL_DROP_THRESHOLD in a
			// row, the socket is genuinely dead from our side; raise
			// xForceReset so the FB tears down the connection and a
			// new client can reconnect. Without this we'd churn
			// forever -- single-client slot stays held by a zombie.
			consecutive_stall_drops := consecutive_stall_drops + 1;
			IF consecutive_stall_drops >= TX_STALL_DROP_THRESHOLD THEN
				tx_stall_force_reset := TRUE;
				consecutive_stall_drops := 0;
			END_IF;
		END_IF;
	END_IF;
END_IF

// --- MsgPack self-test harness hook (Comm task; runs cyclically).
// See doc/2-contracts/msgpack.md and FB_MsgPackTests. Toggle GVL.bRunMsgPackTests
// in online mode to fire; results on GVL.fbMsgPackTests.*.
GVL.fbMsgPackTests(bRun := GVL.bRunMsgPackTests);
