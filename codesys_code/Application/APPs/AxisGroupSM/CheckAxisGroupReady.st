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
    G1ADeg := 0;
END_IF;

// Always apply the machine's coordinate system: on every Ready entry (the
// gate is cleared on UnInited / Error), without waiting for the host's
// SetCoord1 -- a forgotten or failed SetCoord must not leave the arm in
// the kinematic model's frame. A failure of the FB closes the gate and
// raises the FSM error (ProcessFlyEventsAndIo); G1 also checks the
// group's actual transform (CoordFrameOk). SetCoord0 / SetCoord1 still
// work (SetCoord1 re-applies the same frame).
IF AxisGroupReady AND NOT GVL.CoordSystemConfigured AND NOT ApplyCoordTransform
        AND MotionBufferSize = 0 THEN
    CoordTransform.X := 0.0;
    CoordTransform.Y := 0.0;
    CoordTransform.Z := 0.0;
    CoordTransform.A := COORD1_FRAME_A;
    CoordTransform.B := 0.0;
    CoordTransform.C := 0.0;
    ApplyCoordTransform := TRUE;
    GVL.CoordSystemConfigured := TRUE;
    GVL.CoordAutoApplyCount := GVL.CoordAutoApplyCount + 1;
END_IF;
