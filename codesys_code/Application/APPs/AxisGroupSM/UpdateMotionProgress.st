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
//
// PH#5: edge-detection latches PendingMoveDoneId. A separate retry
// block below tries to push every scan until the ring has space, so
// a transient ring-full doesn't lose the event (UI would otherwise
// wait up to 1s for the GET_MACHINE_STATE poll to notice).
IF (PrevMotionBufferSizeForEvent > 0
    AND MotionBufferSize = 0
    AND GroupReadStatusFb.LastAcceptedMovementId <> 0
    AND GroupReadStatusFb.LastAcceptedMovementId <> PrevCompletedMovementId) THEN
    // Newest edge wins: if a previous MOVE_DONE was pending and a new
    // movement just completed before we got to push it, just publish
    // the newer one. UI watches for movement_id gaps and surfaces the
    // skip; losing the older intermediate event is acceptable.
    PendingMoveDoneId := GroupReadStatusFb.LastAcceptedMovementId;
END_IF;
PrevMotionBufferSizeForEvent := MotionBufferSize;

// Retry pump: try until the ring has space. Producer-only on
// reMP_info_ridx -- never consumeTail here.
IF PendingMoveDoneId <> 0 THEN
    IF (GVL.reMP_info_ridx.space() > 0) THEN
        EventPacketPointer := ADR(GVL.reMP_info_arr[GVL.reMP_info_ridx.getHead()]);
        MsgPakInfoClear(EventPacketPointer^);
        EventPacketPointer^.ele_count := 4;
        EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('kind'));
        EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('event'));
        EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('name'));
        EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('MOVE_DONE'));
        EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('movement_id'));
        EventPacketPointer^.whead := MessagePackerFb.PackLINT(pStart := EventPacketPointer^.whead, liValue := TO_LINT(PendingMoveDoneId));
        EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('runtime_ms'));
        EventPacketPointer^.whead := MessagePackerFb.PackLINT(pStart := EventPacketPointer^.whead, liValue := RuntimeMs);
        MsgPakInfoWrapup(EventPacketPointer^);
        GVL.reMP_info_ridx.pushHead();
        PrevCompletedMovementId := PendingMoveDoneId;
        PendingMoveDoneId := 0;
    END_IF;
END_IF;

ShouldReturn := FALSE;
CommandAck := FALSE;
