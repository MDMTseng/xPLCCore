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
// reMP_info_ridx -- never ConsumeTail here.
IF PendingMoveDoneId <> 0 THEN
    // Retry-style emit: keep manual Space() probe so per-scan failure
    // doesn't bump ReMpDropCount (which would count the same packet
    // hundreds of times before PENDING_RETRY_MAX_SCANS gives up).
    // TryAcquireReplySlot doesn't fit this pattern; only the final
    // give-up below bumps the class-specific PendingMoveDoneDropCount.
    IF (GVL.reMP_info_ridx.Space() > 0) THEN
        ResponsePacketPointer := ADR(GVL.reMP_info_arr[GVL.reMP_info_ridx.GetHead()]);
        MsgPakInfoClear(ResponsePacketPointer^);
        PackKvStr(ADR('kind'), ADR('event'));
        PackKvStr(ADR('name'), ADR('MOVE_DONE'));
        PackKvLint(ADR('movement_id'), TO_LINT(PendingMoveDoneId));
        PackKvLint(ADR('runtime_ms'), RuntimeMs);
        CommitReplySlotEvent();
        PrevCompletedMovementId := PendingMoveDoneId;
        PendingMoveDoneId := 0;
        PendingMoveDoneRetryScans := 0;
    ELSE
        // Audit-fix: ring permanently full -> drop and move on. Without
        // this, a stuck consumer would leave PendingMoveDoneId asserted
        // forever and silently swallow every subsequent MOVE_DONE edge
        // (newest-wins overwrites the latch each scan; the UI has no
        // way to know it's stuck).
        //
        // PR review fix: drop ONLY the pending event; do NOT advance
        // PrevCompletedMovementId here. PrevCompletedMovementId is the
        // host-visible last_completed_movement_id used by the §4(2)
        // resume reconcile (scratchpad intent_movement_id vs this
        // field). Advancing it without successfully delivering MOVE_DONE
        // would tell the host "the move that you have an intent for
        // already completed" when in fact the host never saw the event.
        // Worst-case result: renderer skips a blow_off and reel
        // double-advances.
        PendingMoveDoneRetryScans := PendingMoveDoneRetryScans + 1;
        IF PendingMoveDoneRetryScans >= PENDING_RETRY_MAX_SCANS THEN
            PendingMoveDoneId := 0;
            PendingMoveDoneRetryScans := 0;
            GVL.PendingMoveDoneDropCount := GVL.PendingMoveDoneDropCount + 1;
        END_IF;
    END_IF;
END_IF;

ShouldReturn := FALSE;
CommandAck := FALSE;
