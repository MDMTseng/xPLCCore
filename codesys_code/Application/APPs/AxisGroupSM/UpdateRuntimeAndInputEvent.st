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
        IF (GVL.reMP_info_ridx.space() = 0) THEN
            GVL.reMP_info_ridx.consumeTail();
            GVL.ReMpDropCount := GVL.ReMpDropCount + 1;
        END_IF;
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
    END_IF;
    PrevAxisGroupState := TO_INT(AxisGroupManagerFb._eState);
END_IF;

// COORD_SET push event: FALSE->TRUE edge on GVL.CoordSystemConfigured.
// Fires after SetCoord0 / SetCoord1 completes so the UI doesn't have
// to poll GET_MACHINE_STATE to learn the coord gate just opened.
// Envelope matches ST_CHG: kind='event', name='COORD_SET'.
IF (GVL.CoordSystemConfigured AND NOT PrevCoordSet) THEN
    IF (GVL.reMP_info_ridx.space() = 0) THEN
        GVL.reMP_info_ridx.consumeTail();
        GVL.ReMpDropCount := GVL.ReMpDropCount + 1;
    END_IF;
    EventPacketPointer := ADR(GVL.reMP_info_arr[GVL.reMP_info_ridx.getHead()]);
    MsgPakInfoClear(EventPacketPointer^);
    EventPacketPointer^.ele_count := 3;
    EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('kind'));
    EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('event'));
    EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('name'));
    EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('COORD_SET'));
    EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('runtime_ms'));
    EventPacketPointer^.whead := MessagePackerFb.PackLINT(pStart := EventPacketPointer^.whead, liValue := RuntimeMs);
    MsgPakInfoWrapup(EventPacketPointer^);
    GVL.reMP_info_ridx.pushHead();
END_IF;
PrevCoordSet := GVL.CoordSystemConfigured;
