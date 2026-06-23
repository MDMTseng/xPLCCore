(* =========== IMPLEMENTATION =========== *)
RuntimeMs := RuntimeMs + BusPeriod;

// VisuEventControl=0 is the idle/no-op default so the TCP handler
// (ProcessSystemPacket below) can own InputEvent without being
// clobbered by this block on the next cycle. Visualisation buttons
// should write 1..6 momentarily and reset to 0.
CASE VisuEventControl OF
    1: InputEvent := E_RobotEvent.EV_POWER_ON;
    2: InputEvent := E_RobotEvent.EV_GROUP_ENABLE;
    3: InputEvent := E_RobotEvent.EV_HOME_GO;
    4: InputEvent := E_RobotEvent.EV_ERROR;
    5: InputEvent := E_RobotEvent.EV_RESET;
    6: InputEvent := E_RobotEvent.EV_HOME_GO_FORCE_SKIP;
END_CASE;

IF (PreviousInputEvent <> InputEvent) THEN
    AxisGroupManagerFb.TRANSITION(
        eEvent := InputEvent
    );
END_IF;

PreviousInputEvent := InputEvent;
IF TO_INT(AxisGroupManagerFb._eState) <> PrevAxisGroupState THEN
    StateString := TO_STRING(AxisGroupManagerFb._eState);
    // PH#8: latch "from" for the deferred ST_CHG emit instead of pushing
    // inline. The retry pump below tries every scan until the ring has
    // space, so a transient ring-full no longer drops the event. Skip
    // PrevAxisGroupState=-1 (cold-boot sentinel, no "from" to report).
    // Newest-edge-wins: rapid A->B->C while pending overwrites "from"
    // each edge -- UI sees ST_CHG{from=B,to=C}+seq gap and reconciles.
    IF (PrevAxisGroupState >= 0) THEN
        PendingStChgFromState := TO_DINT(PrevAxisGroupState);
    END_IF;
    // PH#2: BLOCK_FOR_* latches BlockCommandId / BlockStartMs / BlockTimeoutMs
    // on first scan of a new CommandId. If the FSM leaves Ready (Error or
    // UnInited via EV_RESET) the in-flight block is implicitly abandoned --
    // ProcessMotionPacket can't run, the request gets NAK'd by the
    // group_not_ready inline path. But the latch persists. If the next
    // session reuses the same CommandId (UI restart resets its counter to 0,
    // or a deterministic id-from-cmd-name scheme), the latch makes the new
    // request inherit the stale BlockStartMs and timeout instantly. Clear
    // the latch on any departure from Ready so the next BLOCK re-latches.
    IF AxisGroupManagerFb._eState <> E_RobotState.Ready THEN
        BlockCommandId := 0;
        BlockStartMs := 0;
        BlockTimeoutMs := 0;
    END_IF;

    // Audit-fix: clear the G1 dedupe ring on entry to Error or UnInited.
    // The (CommandId, MovementId) pairs are scoped to the in-flight session;
    // after a fault or full reset the cached MovementIds correspond to
    // motions that no longer exist. If UI restarts (CommandId counter
    // resets to 0) or a deterministic id-from-cmd-name scheme is used, an
    // unscrubbed ring would replay a stale MovementId on the next G1 with
    // a colliding CommandId -- silent no-op from the operator's view, or
    // worse, the wrong motion appearing to "succeed".
    IF (AxisGroupManagerFb._eState = E_RobotState.Error
        OR AxisGroupManagerFb._eState = E_RobotState.UnInited) THEN
        FOR DupeLoopIdx := 0 TO DUPE_RING_SIZE - 1 DO
            RecentCmdIds[DupeLoopIdx] := 0;
            RecentCmdMoveIds[DupeLoopIdx] := 0;
        END_FOR;
        RecentCmdRingHead := 0;
    END_IF;

    PrevAxisGroupState := TO_INT(AxisGroupManagerFb._eState);
END_IF;

// ST_CHG retry pump (PH#8). PendingStChgFromState >= 0 means an edge is
// waiting to publish; "to" is the current _eState at retry time, which
// under newest-wins always pairs cleanly with the latched "from".
// Producer-only on reMP_info_ridx -- never ConsumeTail here. No drop
// counter bump: ring-full just means "try again next scan", not loss.
IF (PendingStChgFromState >= 0) THEN
    IF ProbeReplySlot() THEN
        StateString := TO_STRING(AxisGroupManagerFb._eState);
        MessagePackerFb.PackKvStr(ADR('kind'), ADR('event'));
        MessagePackerFb.PackKvStr(ADR('name'), ADR('ST_CHG'));
        MessagePackerFb.PackKvDint(ADR('st'), AxisGroupManagerFb._eState);
        MessagePackerFb.PackKvStr(ADR('st_str'), ADR(StateString));
        MessagePackerFb.PackKvDint(ADR('from'), PendingStChgFromState);
        MessagePackerFb.PackKvLint(ADR('runtime_ms'), RuntimeMs);
        CommitReplySlotEvent();
        GVL.StateChangeEventCount := GVL.StateChangeEventCount + 1;
        PendingStChgFromState := -1;
        PendingStChgRetryScans := 0;
    ELSE
        // Bound the retry. Drop after PENDING_RETRY_MAX_SCANS so a
        // permanently-stuck ring doesn't swallow every subsequent
        // ST_CHG edge silently. Newest-wins means the latched "from"
        // would otherwise just keep getting overwritten while never
        // landing.
        PendingStChgRetryScans := PendingStChgRetryScans + 1;
        IF PendingStChgRetryScans >= PENDING_RETRY_MAX_SCANS THEN
            PendingStChgFromState := -1;
            PendingStChgRetryScans := 0;
            GVL.PendingStChgDropCount := GVL.PendingStChgDropCount + 1;
        END_IF;
    END_IF;
END_IF;

// COORD_SET push event: edge-detect on GVL.CoordSystemConfigured (both
// directions). UnInited entry clears the gate; SetCoord0/1 raises it.
// The falling edge matters as much as the rising one -- after a reset
// the operator's first G1 will NAK with err='coord_not_configured'
// unless they reconfigure, so the UI needs to know the gate dropped
// without polling GET_MACHINE_STATE.
// Envelope matches ST_CHG: kind='event', name='COORD_SET', plus a BOOL
// `value` carrying the new gate state.
IF (GVL.CoordSystemConfigured <> PrevCoordSet) THEN
    // Drop-newest on overflow (see UpdateMotionProgress for rationale).
    IF TryAcquireReplySlot(ADR(GVL.ReMpDropCount_CoordSet)) THEN
        MessagePackerFb.PackKvStr(ADR('kind'), ADR('event'));
        MessagePackerFb.PackKvStr(ADR('name'), ADR('COORD_SET'));
        MessagePackerFb.PackKvBool(ADR('value'), GVL.CoordSystemConfigured);
        MessagePackerFb.PackKvLint(ADR('runtime_ms'), RuntimeMs);
        CommitReplySlotEvent();
    END_IF;
END_IF;
PrevCoordSet := GVL.CoordSystemConfigured;

// HEARTBEAT push event. Periodic (HEARTBEAT_INTERVAL_MS, default 500) so
// the UI can detect a dead PLC socket without sending SYS/PING -- and so
// it has a regular cadence to anchor seq-gap detection (if no other event
// fires for seconds, an "expected next seq" still progresses every 500ms).
// Drop-newest on overflow: ring full means the UI has bigger problems
// than a missing heartbeat tick; ReMpDropCount grows and the seq jumps.
IF (LastHeartbeatMs = 0) OR (RuntimeMs - LastHeartbeatMs >= HEARTBEAT_INTERVAL_MS) THEN
    IF TryAcquireReplySlot(ADR(GVL.ReMpDropCount_Heartbeat)) THEN
        MessagePackerFb.PackKvStr(ADR('kind'), ADR('event'));
        MessagePackerFb.PackKvStr(ADR('name'), ADR('HEARTBEAT'));
        MessagePackerFb.PackKvLint(ADR('runtime_ms'), RuntimeMs);
        MessagePackerFb.PackKvLint(ADR('remp_drop_count'), TO_LINT(GVL.ReMpDropCount));
        // Per-type drop breakdown so DiagPanel can tell "lost replies" (real
        // problem) from "lost heartbeat tick" (cosmetic) without polling.
        MessagePackerFb.PackKvLint(ADR('drops_reply'),  TO_LINT(GVL.ReMpDropCount_Reply));
        MessagePackerFb.PackKvLint(ADR('drops_events'), TO_LINT(GVL.ReMpDropCount_StChg + GVL.ReMpDropCount_CoordSet + GVL.ReMpDropCount_Heartbeat + GVL.ReMpDropCount_TriggerEvent));
        CommitReplySlotEvent();
    END_IF;
    LastHeartbeatMs := RuntimeMs;
END_IF;
