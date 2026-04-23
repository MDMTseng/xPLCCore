PROGRAM TCP_MSGPAK_Server
VAR
    // --- Server Configuration ---
    bEnableServer         : BOOL := TRUE;
    ipServerAddress       : NBS.IPv4Address := (ipAddress:='192.168.1.70');
    uiServerPort          : UINT := 8125;

    // --- The Server Function Block Instance ---
    fbMyServer            : FB_TcpMsgPakServer;

    fbMsgUnpacker         : FB_MpUnpacker;      // Unpacker to process the received data packet.
    
    // --- Logic for Packet Flow Control ---
    REEN_CD               : INT := 0;
   
    recv_counter          : INT := 0;
    TMP_PKT_IDX           : UDINT := 0;
	
	
	pkt_type:STRING;
	tmp_MsgPakInfo_ptr:POINTER TO MsgPakInfo;
	
	tmp_ptr:POINTER TO BYTE;
	tmp_aux_ptr:POINTER TO BYTE;
	auxThreadId: DINT := -1;
	id: DINT := 0;
	auxPacketStored: BOOL := FALSE;
	auxAckBuf: ARRAY [0..256-1] OF BYTE;
	auxAckMsg: MsgPakInfo;
	auxAckPacker: FB_MpPacker;
	
	
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
	
	//IF pkt_type==
	pkt_type := '';
	auxThreadId := -1;
	auxPacketStored := FALSE;
	fbMsgUnpacker.Init(ADR(fbMyServer.BUFFER_PTR[0]), fbMyServer.BUFFER_LEN);
	pkt_type := fbMsgUnpacker.TryReadString('type', '');
	auxThreadId := TO_DINT(fbMsgUnpacker.TryReadINT64('thread_id', -1));

	IF (pkt_type = 'AUX' AND auxThreadId >= 0 AND auxThreadId <= 2) THEN
		id := TO_DINT(fbMsgUnpacker.TryReadINT64('id', 0));
		CASE auxThreadId OF
			0:
				IF (GVL.aux0_info_buf_ridx.space() > 0) THEN
					tmp_aux_ptr := ADR(GVL.aux0_info_buf[GVL.aux0_info_buf_ridx.getHead()][0]);
					tmp_aux_ptr[0] := DINT_TO_BYTE(fbMyServer.BUFFER_LEN);
					MEMUtils.MemCpy(ADR(tmp_aux_ptr[1]), ADR(fbMyServer.BUFFER_PTR[0]), DINT_TO_UINT(fbMyServer.BUFFER_LEN));
					GVL.aux0_info_buf_ridx.pushHead();
					auxPacketStored := TRUE;
				END_IF;

			1:
				IF (GVL.aux1_info_buf_ridx.space() > 0) THEN
					tmp_aux_ptr := ADR(GVL.aux1_info_buf[GVL.aux1_info_buf_ridx.getHead()][0]);
					tmp_aux_ptr[0] := DINT_TO_BYTE(fbMyServer.BUFFER_LEN);
					MEMUtils.MemCpy(ADR(tmp_aux_ptr[1]), ADR(fbMyServer.BUFFER_PTR[0]), DINT_TO_UINT(fbMyServer.BUFFER_LEN));
					GVL.aux1_info_buf_ridx.pushHead();
					auxPacketStored := TRUE;
				END_IF;

			2:
				IF (GVL.aux2_info_buf_ridx.space() > 0) THEN
					tmp_aux_ptr := ADR(GVL.aux2_info_buf[GVL.aux2_info_buf_ridx.getHead()][0]);
					tmp_aux_ptr[0] := DINT_TO_BYTE(fbMyServer.BUFFER_LEN);
					MEMUtils.MemCpy(ADR(tmp_aux_ptr[1]), ADR(fbMyServer.BUFFER_PTR[0]), DINT_TO_UINT(fbMyServer.BUFFER_LEN));
					GVL.aux2_info_buf_ridx.pushHead();
					auxPacketStored := TRUE;
				END_IF;
		END_CASE;
		IF (auxPacketStored) THEN
			auxAckMsg := MsgPakInfoInit(
				buff := ADR(auxAckBuf),
				buffer_size := SIZEOF(auxAckBuf),
				useLargeElementCountField := TRUE
			);
			MsgPakInfoClear(auxAckMsg);
			auxAckMsg.ele_count := 2;
			auxAckMsg.whead := auxAckPacker.PackString(pStart := auxAckMsg.whead, psValue := ADR('id'));
			auxAckMsg.whead := auxAckPacker.PackDINT(pStart := auxAckMsg.whead, diValue := id);
			auxAckMsg.whead := auxAckPacker.PackString(pStart := auxAckMsg.whead, psValue := ADR('ack'));
			auxAckMsg.whead := auxAckPacker.PackBool(pStart := auxAckMsg.whead, bValue := TRUE);
			MsgPakInfoWrapup(auxAckMsg);
			fbMyServer.Send(BUFFER_PTR := auxAckMsg.buf, BUFFER_LEN := auxAckMsg.buf_size);
		END_IF;


	ELSE
		tmp_ptr:=ADR(GVL.minfo_buf[GVL.minfo_buf_ridx.getHead()][0]);
		tmp_ptr[0]:=DINT_TO_BYTE(fbMyServer.BUFFER_LEN);//keep data len
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
	ELSIF NOT fbMyServer.CLIENT.xActive THEN
		// Connection is down -- retrying forever is pointless. Drop the
		// backlog so we don't replay stale replies to a future client.
		WHILE GVL.reMP_info_ridx.size() > 0 DO
			GVL.reMP_info_ridx.consumeTail();
		END_WHILE;
	END_IF;
END_IF

// --- MsgPack self-test harness hook (Comm task; runs cyclically).
// See doc/msgpack.md and FB_MsgPackTests. Toggle GVL.bRunMsgPackTests
// in online mode to fire; results on GVL.fbMsgPackTests.*.
GVL.fbMsgPackTests(bRun := GVL.bRunMsgPackTests);
