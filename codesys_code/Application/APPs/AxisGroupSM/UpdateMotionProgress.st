(* =========== IMPLEMENTATION =========== *)
TempMovementId := GroupReadPositionFb.MovementId;
// If either MC read FB is in error, don't propagate its outputs -- they
// may be stale. Freeze buffer size to 0 so ProcessMotionPacket won't
// treat "buffer has room" as "motion is healthy" and accept a new
// move on top of an errored group.
IF GroupReadStatusFb.Error OR GroupReadPositionFb.Error THEN
    MotionBufferSize := 0;
ELSIF (GroupReadPositionFb.MovementId = 0) THEN
    MotionBufferSize := 0;
    CurrentMovementId := GroupReadStatusFb.LastAcceptedMovementId;
    CurrentMovementIdProgress := 1;
    IF (CurrentMovementId = 0) THEN
        CurrentMovementIdProgress := 0;
    END_IF;
ELSE
    // Guard against underflow: both are unsigned SMC_Movement_Id.
    // LastAccepted < current shouldn't happen, but could during
    // a reset race. Clamp to 0 rather than wrapping to huge ULINT.
    IF GroupReadStatusFb.LastAcceptedMovementId >= GroupReadPositionFb.MovementId THEN
        MotionBufferSize := GroupReadStatusFb.LastAcceptedMovementId - GroupReadPositionFb.MovementId;
    ELSE
        MotionBufferSize := 0;
    END_IF;

    IF (CurrentMovementId <> GroupReadPositionFb.MovementId) THEN
        CurrentMovementId := GroupReadPositionFb.MovementId;
    END_IF;

    CurrentMovementIdProgress := GroupReadPositionFb.Position;
END_IF;

IF (CurrentMovementId <> PrevMotionStateId OR CurrentMovementIdProgress <> PrevMotionStateProgress) THEN
    MotionStateString := CONCAT(
        CONCAT(TO_STRING(CurrentMovementId), ' '),
        TO_STRING(CurrentMovementIdProgress)
    );
    PrevMotionStateId := CurrentMovementId;
    PrevMotionStateProgress := CurrentMovementIdProgress;
END_IF;

// MOVE_DONE push event: MotionBufferSize transition >0 -> 0 with a
// non-zero LastAcceptedMovementId means the queued motion drained.
// The UI can gate "ready for next step" on this event instead of
// polling movement_id via GET_MACHINE_STATE at high frequency.
// Envelope matches ST_CHG: kind='event', name='MOVE_DONE'.
IF (PrevMotionBufferSizeForEvent > 0
    AND MotionBufferSize = 0
    AND GroupReadStatusFb.LastAcceptedMovementId <> 0
    AND GroupReadStatusFb.LastAcceptedMovementId <> PrevCompletedMovementId) THEN
    // Drop-newest on overflow (was drop-oldest pre-2026-04-28). Producer
    // (EC_Task) must NOT call consumeTail because Comm task is the sole
    // consumer; two consumers race on _tail. Skip the push and bump the
    // drop counter instead -- the UI reconciles via GET_MACHINE_STATE.
    IF (GVL.reMP_info_ridx.space() > 0) THEN
        EventPacketPointer := ADR(GVL.reMP_info_arr[GVL.reMP_info_ridx.getHead()]);
        MsgPakInfoClear(EventPacketPointer^);
        EventPacketPointer^.ele_count := 4;
        EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('kind'));
        EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('event'));
        EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('name'));
        EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('MOVE_DONE'));
        EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('movement_id'));
        EventPacketPointer^.whead := MessagePackerFb.PackLINT(pStart := EventPacketPointer^.whead, liValue := TO_LINT(GroupReadStatusFb.LastAcceptedMovementId));
        EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('runtime_ms'));
        EventPacketPointer^.whead := MessagePackerFb.PackLINT(pStart := EventPacketPointer^.whead, liValue := RuntimeMs);
        MsgPakInfoWrapup(EventPacketPointer^);
        GVL.reMP_info_ridx.pushHead();
        PrevCompletedMovementId := GroupReadStatusFb.LastAcceptedMovementId;
    ELSE
        GVL.ReMpDropCount := GVL.ReMpDropCount + 1;
    END_IF;
END_IF;
PrevMotionBufferSizeForEvent := MotionBufferSize;

ShouldReturn := FALSE;
CommandAck := FALSE;
