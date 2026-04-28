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
    // Push an ST_CHG event into the reply-ring so the UI doesn't
    // have to poll GA_EV to learn the FSM moved. Skip PrevAxisGroupState=-1
    // which is the cold-boot sentinel (no "from" state to report).
    IF (PrevAxisGroupState >= 0) THEN
        // Drop-newest on overflow: producer (EC_Task) must not consumeTail
        // because Comm task is the sole consumer. See UpdateMotionProgress
        // for the full rationale.
        IF (GVL.reMP_info_ridx.space() > 0) THEN
            EventPacketPointer := ADR(GVL.reMP_info_arr[GVL.reMP_info_ridx.getHead()]);
            MsgPakInfoClear(EventPacketPointer^);
            EventPacketPointer^.ele_count := 6;
            EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('kind'));
            EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('event'));
            EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('name'));
            EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('ST_CHG'));
            EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('st'));
            EventPacketPointer^.whead := MessagePackerFb.PackDINT(pStart := EventPacketPointer^.whead, diValue := AxisGroupManagerFb._eState);
            EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('st_str'));
            EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR(StateString));
            EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('from'));
            EventPacketPointer^.whead := MessagePackerFb.PackDINT(pStart := EventPacketPointer^.whead, diValue := TO_DINT(PrevAxisGroupState));
            EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('runtime_ms'));
            EventPacketPointer^.whead := MessagePackerFb.PackLINT(pStart := EventPacketPointer^.whead, liValue := RuntimeMs);
            MsgPakInfoWrapup(EventPacketPointer^);
            GVL.reMP_info_ridx.pushHead();
            GVL.StateChangeEventCount := GVL.StateChangeEventCount + 1;
        ELSE
            GVL.ReMpDropCount := GVL.ReMpDropCount + 1;
            GVL.ReMpDropCount_StChg := GVL.ReMpDropCount_StChg + 1;
        END_IF;
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

    PrevAxisGroupState := TO_INT(AxisGroupManagerFb._eState);
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
    IF (GVL.reMP_info_ridx.space() > 0) THEN
        EventPacketPointer := ADR(GVL.reMP_info_arr[GVL.reMP_info_ridx.getHead()]);
        MsgPakInfoClear(EventPacketPointer^);
        EventPacketPointer^.ele_count := 4;
        EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('kind'));
        EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('event'));
        EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('name'));
        EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('COORD_SET'));
        EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('value'));
        EventPacketPointer^.whead := MessagePackerFb.PackBool(pStart := EventPacketPointer^.whead, bValue := GVL.CoordSystemConfigured);
        EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('runtime_ms'));
        EventPacketPointer^.whead := MessagePackerFb.PackLINT(pStart := EventPacketPointer^.whead, liValue := RuntimeMs);
        MsgPakInfoWrapup(EventPacketPointer^);
        GVL.reMP_info_ridx.pushHead();
    ELSE
        GVL.ReMpDropCount := GVL.ReMpDropCount + 1;
        GVL.ReMpDropCount_CoordSet := GVL.ReMpDropCount_CoordSet + 1;
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
    IF (GVL.reMP_info_ridx.space() > 0) THEN
        EventPacketPointer := ADR(GVL.reMP_info_arr[GVL.reMP_info_ridx.getHead()]);
        MsgPakInfoClear(EventPacketPointer^);
        EventPacketPointer^.ele_count := 6;
        EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('kind'));
        EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('event'));
        EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('name'));
        EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('HEARTBEAT'));
        EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('runtime_ms'));
        EventPacketPointer^.whead := MessagePackerFb.PackLINT(pStart := EventPacketPointer^.whead, liValue := RuntimeMs);
        EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('remp_drop_count'));
        EventPacketPointer^.whead := MessagePackerFb.PackLINT(pStart := EventPacketPointer^.whead, liValue := TO_LINT(GVL.ReMpDropCount));
        // Per-type drop breakdown so DiagPanel can tell "lost replies" (real
        // problem) from "lost heartbeat tick" (cosmetic) without polling.
        EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('drops_reply'));
        EventPacketPointer^.whead := MessagePackerFb.PackLINT(pStart := EventPacketPointer^.whead, liValue := TO_LINT(GVL.ReMpDropCount_Reply));
        EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('drops_events'));
        EventPacketPointer^.whead := MessagePackerFb.PackLINT(pStart := EventPacketPointer^.whead, liValue := TO_LINT(GVL.ReMpDropCount_StChg + GVL.ReMpDropCount_CoordSet + GVL.ReMpDropCount_Heartbeat + GVL.ReMpDropCount_TriggerEvent));
        MsgPakInfoWrapup(EventPacketPointer^);
        GVL.reMP_info_ridx.pushHead();
    ELSE
        GVL.ReMpDropCount := GVL.ReMpDropCount + 1;
        GVL.ReMpDropCount_Heartbeat := GVL.ReMpDropCount_Heartbeat + 1;
    END_IF;
    LastHeartbeatMs := RuntimeMs;
END_IF;
