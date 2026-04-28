(* =========== IMPLEMENTATION =========== *)
AxisGroupReady := (AxisGroupManagerFb._eState = E_RobotState.Ready);

IF NOT AxisGroupReady THEN
    // Host-authority gate (solidification.md W1 A1). Previously this
    // block called GVL.minfo_buf_ridx.clear() which silently dropped
    // every queued motion packet with no reply -- the UI's
    // sendTcpMsgPack would hang until its own timeout. Drain each
    // packet with an explicit NAK + err='group_not_ready' so the
    // host can distinguish "PLC not ready" from "PLC dead".
    // SYS/AUX paths above already ran and are unaffected.
    WHILE GVL.minfo_buf_ridx.size() > 0 DO
        // Re-parse each slot's header to get CommandId for the echo.
        MessageBufferPointer := ADR(GVL.minfo_buf[GVL.minfo_buf_ridx.getTail()][0]);
        MessageUnpackerFb.Init(ADR(MessageBufferPointer[1]), MessageBufferPointer[0]);
        CommandId := TO_DINT(MessageUnpackerFb.TryReadINT64('id', -1));

        // Drop-newest on overflow (was drop-oldest pre-2026-04-28, see
        // UpdateMotionProgress for the SPSC race rationale). NAK still
        // gets emitted when there's space; under sustained backpressure
        // the UI will see drop-counter climb and reconcile via timeout.
        IF (GVL.reMP_info_ridx.space() > 0) THEN
            ResponsePacketPointer := ADR(GVL.reMP_info_arr[GVL.reMP_info_ridx.getHead()]);
            MsgPakInfoClear(ResponsePacketPointer^);
            ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 3;
            ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err'));
            ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('group_not_ready'));
            ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('id'));
            ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := CommandId);
            ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('ack'));
            ResponsePacketPointer^.whead := MessagePackerFb.PackBool(pStart := ResponsePacketPointer^.whead, bValue := FALSE);
            MsgPakInfoWrapup(ResponsePacketPointer^);
            GVL.reMP_info_ridx.pushHead();
        ELSE
            GVL.ReMpDropCount := GVL.ReMpDropCount + 1;
            GVL.ReMpDropCount_Reply := GVL.ReMpDropCount_Reply + 1;
        END_IF;

        GVL.minfo_buf_ridx.consumeTail();
        GVL.GroupNotReadyNakCount := GVL.GroupNotReadyNakCount + 1;
    END_WHILE;

    MotionPose.c.X := 0;
    MotionPose.c.Y := 0;
    MotionPose.c.Z := 0;
    MotionPose.c.A := 0;
    MotionPose.c.B := 0;
    MotionPose.c.C := 0;
END_IF;
