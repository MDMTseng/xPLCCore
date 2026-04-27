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
	// doc/ringbuf-bugs.md #7.
	send_retry_count: UDINT := 0;
	SEND_MAX_RETRIES: UDINT := 100;

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
    PKT_ALLOW_NEXT:=GVL.minfo_buf_ridx.space()>1
);


// This IF statement will be TRUE for one cycle when a new packet arrives.
IF fbMyServer.PKT_IDX > TMP_PKT_IDX THEN
	REEN_CD:=50;
    recv_counter := recv_counter + 1;

	// Length guard: slots are 256 bytes (1 len + 255 payload). Over-length
	// packets would wrap via DINT_TO_BYTE and corrupt the slot silently.
	// See doc/ringbuf-bugs.md #1. Type/cmd dispatch happens in AxisGroupSM
	// after the slot is drained; unrecognised types NAK with
	// 'missing_type_field'.
	IF (fbMyServer.BUFFER_LEN > GVL.MAX_SLOT_PAYLOAD) THEN
		GVL.OverlenDropCount := GVL.OverlenDropCount + 1;
	ELSIF (GVL.minfo_buf_ridx.space() > 0) THEN
		tmp_ptr:=ADR(GVL.minfo_buf[GVL.minfo_buf_ridx.getHead()][0]);
		tmp_ptr[0]:=DINT_TO_BYTE(fbMyServer.BUFFER_LEN);
		MEMUtils.MemCpy(ADR(tmp_ptr[1]),ADR(fbMyServer.BUFFER_PTR[0]),DINT_TO_UINT(fbMyServer.BUFFER_LEN));
		GVL.minfo_buf_ridx.pushHead();
	END_IF;
END_IF;

// Update the packet index tracker
TMP_PKT_IDX := fbMyServer.PKT_IDX;


DBG_reMP_info_ridx_size:=GVL.reMP_info_ridx.size();
IF GVL.reMP_info_ridx.size()>0 THEN
	tmp_MsgPakInfo_ptr:=ADR(GVL.reMP_info_arr[GVL.reMP_info_ridx.getTail()]);
	// Only consume the slot if the TCP write succeeded. A failure here
	// means the client went away between queueing and this cycle's write;
	// leave the packet in the ring so it can retry next cycle once the
	// connection re-establishes, or be flushed by the reset path if not.
	IF fbMyServer.Send(BUFFER_PTR := tmp_MsgPakInfo_ptr^.buf, BUFFER_LEN := tmp_MsgPakInfo_ptr^.buf_size) THEN
		GVL.reMP_info_ridx.consumeTail();
		send_retry_count := 0;
	ELSIF NOT fbMyServer.CLIENT.xActive THEN
		// Connection is down -- retrying forever is pointless. Drop the
		// backlog so we don't replay stale replies to a future client.
		WHILE GVL.reMP_info_ridx.size() > 0 DO
			GVL.reMP_info_ridx.consumeTail();
		END_WHILE;
		send_retry_count := 0;
	ELSE
		// xActive=TRUE but Send keeps failing -> TX stall / half-open.
		// Drop the head packet after SEND_MAX_RETRIES cycles so the ring
		// doesn't fill up behind a stuck packet. See doc/ringbuf-bugs.md #7.
		send_retry_count := send_retry_count + 1;
		IF send_retry_count >= SEND_MAX_RETRIES THEN
			GVL.reMP_info_ridx.consumeTail();
			GVL.SendStallDropCount := GVL.SendStallDropCount + 1;
			send_retry_count := 0;
		END_IF;
	END_IF;
END_IF

// --- MsgPack self-test harness hook (Comm task; runs cyclically).
// See doc/msgpack.md and FB_MsgPackTests. Toggle GVL.bRunMsgPackTests
// in online mode to fire; results on GVL.fbMsgPackTests.*.
GVL.fbMsgPackTests(bRun := GVL.bRunMsgPackTests);
