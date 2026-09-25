METHOD FlushFlyEvents : BOOL
VAR_INPUT
    notify : BOOL;    // the host is still connected: tell it what was dropped
END_VAR
VAR
    i : INT;
    k : INT;
    p : POINTER TO FlyEventData;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Drop every fly event: they belong to the run (or the session) that just
// ended -- the FSM left Ready, or the host disconnected. Outside Ready they
// are not processed (their TTLs stop), so a stale trigger could otherwise
// fire after recovery. Nothing is dropped silently any more:
//  - a pending WaitForTriggerMotionProgress (ACK_SRC_ID) gets its NAK
//    ('group_not_ready'), like the other deferred waits -- it used to be
//    left unanswered until the host's own timeout;
//  - a pin sequence already under way is fast-forwarded to its last
//    stage, so a strobe, a camera trigger or a blow valve does not stay
//    on; single-stage outputs (the nozzle vacuum) are left as they are
//    -- dropping the vacuum here would drop a held part;
//  - a pin operation that had not fired yet is reported as TRIGGER_ERR
//    (error_code FLY_FLUSHED_ERR) with its event_id.
// The outputs are written out here: in Error the Ready-only IO path does
// not run.
FOR i := 0 TO TO_INT(FlyEventBufferSize) - 1 DO
    p := ADR(FlyEventBuffer[i]);
    IF (p^.TriggerType <> FlyEventTriggerType.NOP)
       AND (p^.TriggerType <> FlyEventTriggerType.Reserved) THEN
        CASE p^.ActionType OF
            FlyEventActType.ACK_SRC_ID:
                IF notify AND TryAcquireReplySlot(ADR(GVL.ReMpDropCount_Reply)) THEN
                    MessagePackerFb.PackKvStr(ADR('err'), ADR('group_not_ready'));
                    MessagePackerFb.PackKvLint(ADR('event_id'), TO_LINT(p^.EventId));
                    CommitReplySlotAck(p^.SourceCommandId, FALSE);
                END_IF;
            FlyEventActType.PIN_OPERATION:
                IF p^.IoStageIndex > 0 THEN
                    FOR k := p^.IoStageIndex TO MIN(p^.IoStageCount, FlyEventStageLimit) - 1 DO
                        DigitalOutputBits := (DigitalOutputBits AND NOT p^.IoStagePinMask[k])
                            OR (p^.IoStagePinState[k] AND p^.IoStagePinMask[k]);
                    END_FOR;
                ELSIF notify AND TryAcquireReplySlot(ADR(GVL.ReMpDropCount_TriggerEvent)) THEN
                    MessagePackerFb.PackKvStr(ADR('kind'), ADR('event'));
                    MessagePackerFb.PackKvStr(ADR('name'), ADR('TRIGGER_ERR'));
                    MessagePackerFb.PackKvDint(ADR('error_code'), FLY_FLUSHED_ERR);
                    MessagePackerFb.PackKvDint(ADR('src_id'), p^.SourceCommandId);
                    MessagePackerFb.PackKvLint(ADR('event_id'), TO_LINT(p^.EventId));
                    CommitReplySlotEvent();
                END_IF;
        END_CASE;
    END_IF;
    p^.ActionType := FlyEventActType.NOP;
    p^.TriggerType := FlyEventTriggerType.NOP;
END_FOR;
OutputCHs[1] := TO_BYTE(DigitalOutputBits AND 16#FF);
OutputCHs[2] := TO_BYTE(SHR(DigitalOutputBits, 8) AND 16#FF);
GVL.FlyFlushCount := GVL.FlyFlushCount + 1;
