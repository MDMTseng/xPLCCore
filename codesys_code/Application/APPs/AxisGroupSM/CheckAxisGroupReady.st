(* =========== IMPLEMENTATION =========== *)
AxisGroupReady := (AxisGroupManagerFb._eState = E_RobotState.Ready);

IF NOT AxisGroupReady THEN
    // Host-authority gate (solidification.md W1 A1). Previously this
    // block called GVL.minfo_buf_ridx.Clear() which silently dropped
    // every queued motion packet with no reply -- the UI's
    // sendTcpMsgPack would hang until its own timeout. Drain each
    // packet with an explicit NAK + err='group_not_ready' so the
    // host can distinguish "PLC not ready" from "PLC dead".
    // SYS/AUX paths above already ran and are unaffected.
    WHILE GVL.minfo_buf_ridx.Size() > 0 DO
        // Re-parse each slot's header to get CommandId for the echo.
        MessageBufferPointer := ADR(GVL.minfo_buf[GVL.minfo_buf_ridx.GetTail()][0]);
        MessageUnpackerFb.Init(ADR(MessageBufferPointer[1]), MessageBufferPointer[0]);
        CommandId := TO_DINT(MessageUnpackerFb.TryReadINT64('id', -1));

        // Drop-newest on overflow (was drop-oldest pre-2026-04-28, see
        // UpdateMotionProgress for the SPSC race rationale). NAK still
        // gets emitted when there's space; under sustained backpressure
        // the UI will see drop-counter climb and reconcile via timeout.
        IF TryAcquireReplySlot(ADR(GVL.ReMpDropCount_Reply)) THEN
            MessagePackerFb.PackKvStr(ADR('err'), ADR('group_not_ready'));
            CommitReplySlotAck(CommandId, FALSE);
        END_IF;

        GVL.minfo_buf_ridx.ConsumeTail();
        GVL.GroupNotReadyNakCount := GVL.GroupNotReadyNakCount + 1;
    END_WHILE;

    MotionPose.c.X := 0;
    MotionPose.c.Y := 0;
    MotionPose.c.Z := 0;
    MotionPose.c.A := 0;
    MotionPose.c.B := 0;
    MotionPose.c.C := 0;
END_IF;
