
PROGRAM AxisGroupSM
    VAR CONSTANT
        FlyEventBufferSize: UDINT := 10;
        TaskInstructionBufferSize: UDINT := 10;
        FlyEventStageLimit: INT := 9;

        // Motion buffer fullness threshold: reject new 'M' packets when the
        // SoftMotion command buffer has this many queued moves (back-pressure).
        MOTION_BUFFER_THRESHOLD : ULINT := 12;
        // Cooldown (ms) before re-trying a motion command after the FB
        // refused .CommandAccepted (e.g. GroupWait, MoveLinearAbsolute).
        RETRY_COOLDOWN_G4_MS    : DINT := 100;
        RETRY_COOLDOWN_G1_MS    : DINT := 101;
        // Error code reported on FlyEvent TimeToLive expiry.
        TRIGGER_TIMEOUT_ERR     : INT := 100;
        // Sentinel used by 'G1' to detect "A not supplied" in the TryReadREAL
        // default. Any real A value is << |919191|.
        G1_A_UNSET_SENTINEL     : LREAL := -919191;
        // Bound on per-scan SYS-drain iterations. Prevents a flood of SYS
        // packets from monopolising the EC task. 16 covers normal bursts
        // (PING + GA_EV + GET_MACHINE_STATE x a few); leftover slots wait
        // one scan -- benign because SYS replies aren't latency-critical.
        SYS_DRAIN_MAX_PER_SCAN  : INT := 16;
    END_VAR
    VAR
        reelpullmotor_fActPosition:REAL;
		OutputCHs:ARRAY[0..15] OF USINT;
		InputCHs:ARRAY[0..15] OF USINT;
		RuntimeMs: LINT := 0;
        BusPeriod: DINT := 1;
        AxisGroupReady: BOOL := FALSE;

        AxisGroupManagerFb: AxisGroupManager(
            agAxisGroup := AxisGroupGVL_agSpiderR.SpiderR,
			axReel:=reelpullmotor);

        GroupActualPositionFb: SM3_Robotics.MC_GroupReadActualPosition;
        ReadPosition: SM3_Robotics.SMC_POS_REF;

        SetCoordTransformFb : MC_SetCoordinateTransform;
        ReadMcsTransformFb : MC_ReadCoordinateTransform;
        ApplyCoordTransform : BOOL := FALSE;
        CoordTransform : MC_COORD_REF;

        InputEvent: E_RobotEvent := E_RobotEvent.EV_NONE;
        PreviousInputEvent: E_RobotEvent := E_RobotEvent.EV_NONE;
        VisuEventControl: INT;
        StateString: STRING;

        MotionStateString: STRING;
		
		
        reelMoveRelative : MC_MoveRelative;
        reelMoveRelative2: MC_MoveRelative;   // second instance: SoftMotion forbids >1 movement per FB
        // Request-pattern vars for cyclic reelMoveRelative
        reelGoRequest  : BOOL  := FALSE;
        reelGoRequest2 : BOOL  := FALSE;
        reelGoDistance : LREAL := 0;
        reelGoVelocity : LREAL := 0;
        reelGoAccel    : LREAL := 100;
        reelGoDecel    : LREAL := 100;
        reelGoJerk     : LREAL := 1000;

        MoveLinearAbsoluteFb: SM3_Robotics.MC_MoveLinearAbsolute;
        GroupWaitFb: SM3_Robotics.SMC_GroupWait;

        MotionPose: SM3_Robotics.SMC_POS_REF := ( c := (x := 0.0, y := 0.0, z := 0.0, a := 0.0));

        PacketType: STRING;
        CommandType: STRING;
        CommandId: DINT := 0;
        ShouldReturn: BOOL := FALSE;
        CommandAck: BOOL := FALSE;

        MessageUnpackerFb: FB_MpUnpacker;
        fbPinOpSeqUnpacker: FB_MpUnpacker; // Helper unpacker for pin_op_seq array

        FlyEventBuffer: ARRAY [0..FlyEventBufferSize-1] OF FlyEventData; // event queue for motion-triggered IO
        FlyEventAvailableCount: INT;

        // TaskInstructionBuffer: ARRAY [0..TaskInstructionBufferSize-1] OF TaskInstructionData; 
        // TaskInstructionBuffer_ridx:FB_RingBufferIndex(TaskInstructionBufferSize);



        RetryMotionCommandCooldown: DINT := 0;

        LatestFeedRate: LREAL := 10000;
        LatestAcceleration: LREAL := 10000;
        LatestDeceleration: LREAL := 10000;
        LatestJerk: LREAL := 10000;
        LatestFactor: LREAL := 1;

        CornerTransition: REAL := 3;

        GroupReadStatusFb: SM3_Robotics.MC_GroupReadStatus;
        GroupReadPositionFb: SM3_Robotics.SMC_GroupReadPositionInMovement;
        GroupReadPositionFb2: SM3_Robotics.SMC_GroupReadPositionInMovement;

        MotionBufferSize: ULINT;
        CurrentMovementId: SMC_Movement_Id;
        CurrentMovementIdProgress: REAL;

        MessagePackerFb: FB_MpPacker;
        ResponsePacketPointer: POINTER TO MsgPakInfo;

        // MaxVelocity: REAL := 1000;
        // MaxAcceleration: REAL := 4000;
        // MaxJerk: REAL := 4000000;

        DigitalOutputBits: ULINT;
        TmpDigitalOutputBits: ULINT;

        IoTriggerCount: INT := 0;
        IoTriggerResetCount: INT := 0;
        IoTriggerInfoCount: INT := 0;
        IoCommandCount: INT := 0;

        DigitalInputBits_Curr: ULINT := 0;
        DigitalInputBits_Prev: ULINT := 0;
        DigitalInputFlipCounts: ARRAY[0..15] OF UDINT;

        PrevAxisGroupState: INT := -1; // Force initial StateString update

        // A2: PLC-side timeout for BLOCK_FOR_* packets.
        // Without this a BLOCK packet sits in minfo_buf forever if the
        // condition (motion stop / digital input) never becomes true,
        // which hangs the UI until reconnect. Packet carries optional
        // `timeout_ms` (absent or 0 = wait forever = legacy behavior).
        BlockStartMs: LINT := 0;
        BlockCommandId: DINT := 0;
        BlockTimeoutMs: LINT := 0;

        // Push-notification (server-initiated event) packet pointer.
        // Used by the state-change detector to emit ST_CHG events into the
        // reMP reply-ring so the UI learns about FSM transitions without
        // polling GA_EV. Event packets carry `kind='event'` and no `id`.
        EventPacketPointer: POINTER TO MsgPakInfo;

        // Edge-detect state for COORD_SET and MOVE_DONE push events.
        // Both emit into the same reMP reply-ring as ST_CHG, same
        // `kind='event'` envelope, so the UI can consume them through
        // the existing `plc:event` dispatch without polling.
        PrevCoordSet: BOOL := FALSE;
        PrevMotionBufferSizeForEvent: ULINT := 0;
        PrevCompletedMovementId: ULINT := 0;


















    Index : INT;
    StageLoopIdx : INT;
    PinOpSeqIdx : INT; // Index for iterating through pin_op_seq array
    
    TempMovementId : SMC_Movement_Id;
    
    MessageBufferPointer : POINTER TO BYTE;


    TriggerActive : BOOL;
    TriggerErrorCode : INT;
    TempByte : BYTE;
    DigitalOutputPointer : POINTER TO BYTE;
    
        MotionOutputPin: ULINT;
        StagePinState: ULINT;
        StagePinMaskTemp: ULINT;
        StagePinStateTemp: ULINT;
        StageDelayTemp: DINT;



    //LocVar FOR ProcessMotionPacket
    TransitionParameter : ARRAY [0..1] OF LREAL := [0, 0];
    FlyEventTempData : FlyEventData;
    MotionPoseTempReal : REAL;
    PrevMotionStateId : SMC_Movement_Id; // For MotionStateString change detection
    PrevMotionStateProgress : REAL := -1.0; // Force initial update
    G1AbortMode : BOOL; // TRUE = BufferMode.Aborting, interrupts current move


    
    G4HaltTime: REAL;
    InputPinMask: UINT;
    InputPinGroup: UINT;
    InputPinState: UINT;
    
    MotionTargetMovementId: SMC_Movement_Id;
    
    DigitalInputPointer: POINTER TO BYTE;



    DBG_PIN: UINT;

    // W1 A5 per-packet protocol version gate. ProtocolVersionGot=0 means
    // absent (legacy, allowed); anything else must equal GVL.PROTOCOL_VERSION
    // or the packet is NAK'd before dispatch.
    ProtocolVersionGot: LINT;

    // Per-scan iteration counter for the SYS-drain loop (see ReadIncomingPacket
    // block). Bounded by SYS_DRAIN_MAX_PER_SCAN so a runaway SYS flood can't
    // monopolise the EC task.
    SysDrainIter : INT;

    // B1 diagnostic: edge tracker on ApplyCoordTransform so DBG counters
    // can capture the rising edge of SetCoordTransformFb.Execute. Reset
    // alongside ApplyCoordTransform.
    PrevApplyCoordTransform : BOOL := FALSE;
    AxesErrMask             : UDINT := 0;
    PingGapMs               : UDINT := 0;
    DbgSetCoordSeenBusy     : BOOL := FALSE;
    DbgSetCoordSeenDone     : BOOL := FALSE;
    DbgSetCoordSeenError    : BOOL := FALSE;

    DBG_BLOCK : BOOL:=TRUE;

    END_VAR
(* =========== IMPLEMENTATION =========== *)

//IMPORTANT:following if TRUE... and if FALSE.... are mandatory, DO not try to remove/optimize it


    // B1 diagnostic: per-scan tick. If this stops advancing relative to
    // wall clock, the cyclic task itself is stalled (vs. the FB returning
    // quickly while something downstream blocks).
    GVL.AxisGroupSMScans := GVL.AxisGroupSMScans + 1;

    // Per-axis drive-interface error latch (W5 PLC half follow-on).
    // Cyclic copy so GET_MACHINE_STATE stays a pure read. Field probed
    // live: ErrorIdent.* doesn't exist on SM_Drive_GenericDSP402;
    // uiDriveInterfaceError is the DS402 fault-code mirror and is the
    // only diagnostic field that resolves on these axes. See
    // codesys_scripts/jobs/templates/probe_axis_errorident2.py.
    GVL.LastAxesErrorIDs[0] := IoConfig_Globals.EAxis0.uiDriveInterfaceError;
    GVL.LastAxesErrorIDs[1] := IoConfig_Globals.EAxis1.uiDriveInterfaceError;
    GVL.LastAxesErrorIDs[2] := IoConfig_Globals.EAxis2.uiDriveInterfaceError;
    GVL.LastAxesErrorIDs[3] := reelpullmotor.uiDriveInterfaceError;

    InitRuntime();

    // Virtual-motors TTL gate. Derive GVL.bVirtualMotorsMode from
    // GVL.bVirtualMotorsMode_Request (which the operator forces via
    // CODESYS online mode) and a self-expiring timer. Even if the force
    // is left on indefinitely, the derived flag snaps back to FALSE
    // after VIRTUAL_MOTORS_TTL so a forgotten override can't leak into
    // a real-motor session without a cold boot. See GVL.st for docs.
    GVL.fbVirtualMotorsExpire(IN := GVL.bVirtualMotorsMode_Request, PT := GVL.VIRTUAL_MOTORS_TTL);
    GVL.bVirtualMotorsMode := GVL.bVirtualMotorsMode_Request AND NOT GVL.fbVirtualMotorsExpire.Q;


    //UpdateRuntimeAndInputEvent();
    IF TRUE THEN
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
    END_IF;


    UpdateAxisGroupState();


    // Global error supervisor: the per-state checks in AxisGroupManager.Update
    // only watch the power FBs. A move that goes into errorstop (axis fault,
    // bus drop mid-motion, servo trip) surfaces on GroupReadStatusFb.Error /
    // GroupReadPositionFb.Error, and a bus-level fault on
    // IoConfig_Globals.EtherCAT_Master_SoftMotion.xError. Catch those here
    // and drive the FSM to Error -- guarded so we don't loop while already
    // in Error/UnInited. Bus errors take priority so the error source is
    // diagnostic rather than a downstream symptom.
    IF (AxisGroupManagerFb._eState <> E_RobotState.Error
        AND AxisGroupManagerFb._eState <> E_RobotState.UnInited) THEN
        IF IoConfig_Globals.EtherCAT_Master_SoftMotion.xError THEN
            GVL.LastErrorSource := 'Supervisor:EtherCAT_Master';
            GVL.LastErrorID := 0;
            AxisGroupManagerFb.Transition(E_RobotEvent.EV_ERROR);
        ELSIF GroupReadStatusFb.Error THEN
            GVL.LastErrorSource := 'Supervisor:GroupReadStatus';
            GVL.LastErrorID := TO_DWORD(GroupReadStatusFb.ErrorID);
            AxisGroupManagerFb.Transition(E_RobotEvent.EV_ERROR);
        ELSIF GroupReadPositionFb.Error THEN
            GVL.LastErrorSource := 'Supervisor:GroupReadPosition';
            GVL.LastErrorID := TO_DWORD(GroupReadPositionFb.ErrorID);
            AxisGroupManagerFb.Transition(E_RobotEvent.EV_ERROR);
        ELSIF (AxisGroupManagerFb._eState = E_RobotState.Ready
               AND GVL.LastUiPingMs > 0
               AND (RuntimeMs - GVL.LastUiPingMs) > GVL.UI_HEARTBEAT_TIMEOUT_MS) THEN
            // W1 A3 (PLC supervisor): UI went silent while we were accepting
            // its commands. Drop to Error so MotionBufferSize freezes and any
            // in-flight motion winds down via the Error-state GroupDisable +
            // Reset cycle. Operator recovers via EV_RESET -> UnInited -> ...
            // when the UI reconnects.
            GVL.LastErrorSource := 'Supervisor:UiHeartbeatStale';
            GVL.LastErrorID := TO_DWORD(RuntimeMs - GVL.LastUiPingMs);
            GVL.UiHeartbeatStaleCount := GVL.UiHeartbeatStaleCount + 1;
            AxisGroupManagerFb.Transition(E_RobotEvent.EV_ERROR);
        END_IF;
    END_IF;


    //UpdateMotionProgress();
    IF TRUE THEN
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
            IF (GVL.reMP_info_ridx.space() = 0) THEN
                GVL.reMP_info_ridx.consumeTail();
                GVL.ReMpDropCount := GVL.ReMpDropCount + 1;
            END_IF;
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
        END_IF;
        PrevMotionBufferSizeForEvent := MotionBufferSize;

        ShouldReturn := FALSE;
        CommandAck := FALSE;

    END_IF;


    // Compute AxisGroupReady early so the unified packet-drain loop below
    // can decide whether motion packets should be NAK'd as 'group_not_ready'
    // (legacy behavior) or left at the tail for ProcessMotionPacket. The
    // CheckAxisGroupReady block further down uses the same flag for the
    // MotionPose reset and the early RETURN -- it's safe to compute here
    // because the FSM state can't change mid-scan.
    AxisGroupReady := (AxisGroupManagerFb._eState = E_RobotState.Ready);

    //ReadIncomingPacket() + ProcessSystemPacket() + not-ready drain.
    // Single drain loop over minfo_buf that:
    //   - dispatches SYS packets (PING/GA_EV/GET_MACHINE_STATE/etc.)
    //   - NAKs protocol_version_mismatch packets
    //   - NAKs motion packets with 'group_not_ready' when FSM isn't Ready
    //   - leaves motion at the tail for ProcessMotionPacket when Ready
    // Fixes the prior anomaly where a PING queued behind a stalled motion
    // packet got false-NAK'd as 'group_not_ready' (the old not-ready drain
    // chewed through every slot blindly).
    PacketType := '';
    CommandType := '';
    ResponsePacketPointer := 0;
    SysDrainIter := 0;
    WHILE (GVL.minfo_buf_ridx.size() > 0) AND (SysDrainIter < SYS_DRAIN_MAX_PER_SCAN) DO
        MessageBufferPointer := ADR(GVL.minfo_buf[GVL.minfo_buf_ridx.getTail()][0]);
        MessageUnpackerFb.Init(ADR(MessageBufferPointer[1]), MessageBufferPointer[0]);
        CommandId := TO_DINT(MessageUnpackerFb.TryReadINT64('id', - 1));
        PacketType := MessageUnpackerFb.TryReadString('type', '');
        CommandType := MessageUnpackerFb.TryReadString('cmd', '');
        ProtocolVersionGot := MessageUnpackerFb.TryReadINT64('protocol_version', 0);

        // W1 A5: if the client declared a version and it doesn't match,
        // NAK before any dispatch. 0 (absent / legacy) is allowed through.
        IF (ProtocolVersionGot <> 0 AND TO_UDINT(ProtocolVersionGot) <> GVL.PROTOCOL_VERSION) THEN
            IF (GVL.reMP_info_ridx.space() = 0) THEN
                GVL.reMP_info_ridx.consumeTail();
                GVL.ReMpDropCount := GVL.ReMpDropCount + 1;
            END_IF;
            ResponsePacketPointer := ADR(GVL.reMP_info_arr[GVL.reMP_info_ridx.getHead()]);
            MsgPakInfoClear(ResponsePacketPointer^);
            ResponsePacketPointer^.ele_count := 4;
            ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err'));
            ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('protocol_version_mismatch'));
            ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err_got'));
            ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := ProtocolVersionGot);
            ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('id'));
            ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := CommandId);
            ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('ack'));
            ResponsePacketPointer^.whead := MessagePackerFb.PackBool(pStart := ResponsePacketPointer^.whead, bValue := FALSE);
            MsgPakInfoWrapup(ResponsePacketPointer^);
            GVL.reMP_info_ridx.pushHead();

            GVL.minfo_buf_ridx.consumeTail();
            GVL.ProtocolVersionMismatchCount := GVL.ProtocolVersionMismatchCount + 1;

            // Neutralise downstream dispatch in case loop exits next.
            PacketType := '';
            CommandType := '';
            ResponsePacketPointer := 0;
            SysDrainIter := SysDrainIter + 1;

        ELSIF (PacketType = 'SYS') THEN
            // reMP overflow guard: if the reply ring is full, drop the oldest
            // queued reply to make room. getHead() returning -1 would index
            // reMP_info_arr[-1] and fault the task. See doc/ringbuf-bugs.md #4.
            IF (GVL.reMP_info_ridx.space() = 0) THEN
                GVL.reMP_info_ridx.consumeTail();
                GVL.ReMpDropCount := GVL.ReMpDropCount + 1;
            END_IF;
            ResponsePacketPointer := ADR(GVL.reMP_info_arr[GVL.reMP_info_ridx.getHead()]); // prepare for return data buffer

            MsgPakInfoClear(ResponsePacketPointer^);

            IF (CommandType = 'GA_EV') THEN
                ShouldReturn := TRUE;
                CommandAck := TRUE;
                InputEvent := TO_UDINT(MessageUnpackerFb.TryReadINT64('ev', - 1));
                AxisGroupManagerFb.TRANSITION(
                    eEvent := InputEvent
                );

                ShouldReturn := TRUE;
                CommandAck := TRUE;
                ResponsePacketPointer^.ele_count := 4;
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('st'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := AxisGroupManagerFb._eState);
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('st_str'));
                StateString := TO_STRING(AxisGroupManagerFb._eState);
                // NOTE: PrevAxisGroupState is intentionally NOT updated here.
                // The ST_CHG push-event detector above at line ~255 owns that
                // variable; pre-updating it would suppress the ST_CHG event
                // for any transition triggered via SYS/GA_EV (verified live
                // 2026-04-24: UnInited->Powering was silently swallowed).
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR(StateString));
                // Error latch: empty string + 0 when no error has been recorded
                // since the last EV_RESET -> UnInited cycle.
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err_src'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR(GVL.LastErrorSource));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err_id'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.LastErrorID));
            ELSIF (CommandType = 'PING') THEN
                // A3: UI heartbeat. Stamp last-seen and bump counter; reply with runtime.
                // Track worst-case PING-to-PING gap (PLC clock) for comm-jitter
                // visibility. Skip first sample after reset (PrevPingRuntimeMs=0).
                IF (GVL.PrevPingRuntimeMs > 0) THEN
                    PingGapMs := TO_UDINT(RuntimeMs - GVL.PrevPingRuntimeMs);
                    IF (PingGapMs > GVL.PingMaxGapMs) THEN
                        GVL.PingMaxGapMs := PingGapMs;
                    END_IF;
                END_IF;
                GVL.PrevPingRuntimeMs := RuntimeMs;
                GVL.LastUiPingMs := RuntimeMs;
                GVL.UiPingCount  := GVL.UiPingCount + 1;
                ShouldReturn := TRUE;
                CommandAck := TRUE;
                ResponsePacketPointer^.ele_count := 2;
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('pong'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackBool(pStart := ResponsePacketPointer^.whead, bValue := TRUE);
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('runtime_ms'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := RuntimeMs);
            ELSIF (CommandType = 'GET_MACHINE_STATE') THEN
                // A4: Full-snapshot read for UI reconnect. Doesn't change FSM.
                ShouldReturn := TRUE;
                CommandAck := TRUE;
                StateString := TO_STRING(AxisGroupManagerFb._eState);
                // Per-axis fault snapshot (W5 PLC half). DS402 drives on this
                // bus don't expose nErrorID/LastError (verified live), so we
                // surface bError as a 4-bit mask + nAxisState per axis. UI can
                // tell the operator "Axis 2 errorstop" instead of "Error".
                AxesErrMask := 0;
                IF IoConfig_Globals.EAxis0.bError THEN AxesErrMask := AxesErrMask OR 16#1; END_IF;
                IF IoConfig_Globals.EAxis1.bError THEN AxesErrMask := AxesErrMask OR 16#2; END_IF;
                IF IoConfig_Globals.EAxis2.bError THEN AxesErrMask := AxesErrMask OR 16#4; END_IF;
                IF reelpullmotor.bError           THEN AxesErrMask := AxesErrMask OR 16#8; END_IF;
                ResponsePacketPointer^.ele_count := 11;
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('st'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := AxisGroupManagerFb._eState);
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('st_str'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR(StateString));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err_src'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR(GVL.LastErrorSource));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err_id'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.LastErrorID));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('motion_buffer_size'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := TO_LINT(MotionBufferSize));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('movement_id'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := TO_LINT(GroupReadPositionFb.MovementId));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('runtime_ms'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := RuntimeMs);
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('coord_set'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackBool(pStart := ResponsePacketPointer^.whead, bValue := GVL.CoordSystemConfigured);
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('axes_err_mask'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(AxesErrMask));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('axes_state'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue :=
                    TO_DINT(IoConfig_Globals.EAxis0.nAxisState)
                    OR (TO_DINT(IoConfig_Globals.EAxis1.nAxisState) * 256)
                    OR (TO_DINT(IoConfig_Globals.EAxis2.nAxisState) * 65536)
                    OR (TO_DINT(reelpullmotor.nAxisState) * 16777216));
                // axes_err_id : msgpack array of 4 ints (DS402
                // uiDriveInterfaceError per axis, latched cyclically into
                // GVL.LastAxesErrorIDs). Same axis ordering as axes_err_mask
                // bits 0..3. UINT zero-extends cleanly into DINT for wire.
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('axes_err_id'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackArrayHeader(pStart := ResponsePacketPointer^.whead, uiCount := 4);
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := UINT_TO_DINT(GVL.LastAxesErrorIDs[0]));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := UINT_TO_DINT(GVL.LastAxesErrorIDs[1]));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := UINT_TO_DINT(GVL.LastAxesErrorIDs[2]));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := UINT_TO_DINT(GVL.LastAxesErrorIDs[3]));
            ELSIF (CommandType = 'RESET_DBG_INFO') THEN
                // SYS-side reset: clears comm-stability counters without
                // requiring FSM=Ready (the motion-side RESET_DBG_INFO at the
                // bottom of this file is a legacy branch and only fires from
                // the M dispatcher path). Generic comm hygiene; lets the UI
                // dashboard rebaseline at any time.
                GVL.ReMpDropCount               := 0;
                GVL.OverlenDropCount            := 0;
                GVL.SendStallDropCount          := 0;
                GVL.GroupNotReadyNakCount       := 0;
                GVL.MissingTypeFieldNakCount    := 0;
                GVL.CoordNotConfiguredNakCount  := 0;
                GVL.ProtocolVersionMismatchCount := 0;
                GVL.IdleResetCount              := 0;
                GVL.ReadErrorResetCount         := 0;
                GVL.ParserErrorResetCount       := 0;
                GVL.UiHeartbeatStaleCount       := 0;
                GVL.StateChangeEventCount       := 0;
                GVL.PingMaxGapMs                := 0;
                GVL.PrevPingRuntimeMs           := 0;
                ShouldReturn := TRUE;
                CommandAck := TRUE;
                ResponsePacketPointer^.ele_count := 1;
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('reset'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackBool(pStart := ResponsePacketPointer^.whead, bValue := TRUE);
            ELSIF (CommandType = 'GET_DIAG') THEN
                // Generic comm/robustness counters dump. UI dashboard polls this
                // (e.g. once per second) to chart drop/NAK/reset rates and the
                // PING gap-jitter envelope. Read-only; doesn't perturb FSM.
                // Counters resettable via SYS/RESET_DBG_INFO (see existing handler).
                ShouldReturn := TRUE;
                CommandAck := TRUE;
                ResponsePacketPointer^.ele_count := 17;
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('runtime_ms'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := RuntimeMs);
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('sm_scans'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.AxisGroupSMScans));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('remp_drop'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.ReMpDropCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('overlen_drop'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.OverlenDropCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('send_stall_drop'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.SendStallDropCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('group_not_ready_nak'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.GroupNotReadyNakCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('missing_type_nak'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.MissingTypeFieldNakCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('coord_not_cfg_nak'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.CoordNotConfiguredNakCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('proto_mismatch_nak'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.ProtocolVersionMismatchCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('idle_reset'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.IdleResetCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('read_err_reset'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.ReadErrorResetCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('parser_err_reset'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.ParserErrorResetCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('ui_ping_count'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.UiPingCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('ui_hb_stale_count'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.UiHeartbeatStaleCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('ping_max_gap_ms'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.PingMaxGapMs));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('last_ui_ping_ms'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := GVL.LastUiPingMs);
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('st_chg_event_count'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.StateChangeEventCount));
            ELSE
                // Unknown SYS command -- emit NAK so the TS caller doesn't hang on timeout.
                ShouldReturn := TRUE;
                CommandAck := FALSE;
                ResponsePacketPointer^.ele_count := 1;
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('unknown SYS cmd'));
            END_IF;

            GVL.minfo_buf_ridx.consumeTail();

            IF ShouldReturn = TRUE THEN // return ack/nak with id
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('id'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := CommandId);

                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('ack'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackBool(pStart := ResponsePacketPointer^.whead, bValue := CommandAck);

                ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 2;
                MsgPakInfoWrapup(ResponsePacketPointer^);
                GVL.reMP_info_ridx.pushHead();
            END_IF;
            SysDrainIter := SysDrainIter + 1;

        ELSIF (PacketType <> 'M') THEN
            // Tail is neither SYS nor 'M'. Most likely a caller forgot to set
            // type:'M' on a motion command (closed B1 ghost: SetCoord1 sent
            // as {cmd:'SetCoord1'} with no type sat at the tail forever,
            // blocked PINGs, supervisor tripped at 5s). NAK inline so a
            // future caller fails fast instead of "stalling" -- and so any
            // SYS packets queued behind it can drain in this same loop.
            IF (GVL.reMP_info_ridx.space() = 0) THEN
                GVL.reMP_info_ridx.consumeTail();
                GVL.ReMpDropCount := GVL.ReMpDropCount + 1;
            END_IF;
            ResponsePacketPointer := ADR(GVL.reMP_info_arr[GVL.reMP_info_ridx.getHead()]);
            MsgPakInfoClear(ResponsePacketPointer^);
            ResponsePacketPointer^.ele_count := 4;
            ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err'));
            ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('missing_type_field'));
            ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err_got_type'));
            ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR(PacketType));
            ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('id'));
            ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := CommandId);
            ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('ack'));
            ResponsePacketPointer^.whead := MessagePackerFb.PackBool(pStart := ResponsePacketPointer^.whead, bValue := FALSE);
            MsgPakInfoWrapup(ResponsePacketPointer^);
            GVL.reMP_info_ridx.pushHead();

            GVL.minfo_buf_ridx.consumeTail();
            GVL.MissingTypeFieldNakCount := GVL.MissingTypeFieldNakCount + 1;

            PacketType := '';
            CommandType := '';
            ResponsePacketPointer := 0;
            SysDrainIter := SysDrainIter + 1;

        ELSE
            // Tail is type:'M' (motion). When FSM isn't Ready, NAK it inline
            // with 'group_not_ready' so the next iteration can see any SYS
            // packets queued behind it. When Ready, EXIT and let
            // ProcessMotionPacket handle the tail.
            IF NOT AxisGroupReady THEN
                IF (GVL.reMP_info_ridx.space() = 0) THEN
                    GVL.reMP_info_ridx.consumeTail();
                    GVL.ReMpDropCount := GVL.ReMpDropCount + 1;
                END_IF;
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

                GVL.minfo_buf_ridx.consumeTail();
                GVL.GroupNotReadyNakCount := GVL.GroupNotReadyNakCount + 1;

                PacketType := '';
                CommandType := '';
                ResponsePacketPointer := 0;
                SysDrainIter := SysDrainIter + 1;
            ELSE
                EXIT;
            END_IF;
        END_IF;
    END_WHILE;


	
	
	
	
   //OutputCHs[1]:=InputCHs[1];
   //OutputCHs[2]:=InputCHs[2];
   //OutputCHs[3]:=InputCHs[2];
	
	
   // DigitalInputPointer := IoConfig_Globals.HECAT_1616.InputData;//////////////////////////////////////////////

    IF DigitalInputPointer <> 0 THEN//TrackDigitalInputFlips
        DigitalInputBits_Curr := TO_ULINT(DigitalInputPointer[0]) OR SHL(TO_ULINT(DigitalInputPointer[1]), 8);
        IF RuntimeMs > 1 THEN
            TmpDigitalOutputBits := DigitalInputBits_Curr XOR DigitalInputBits_Prev;
            IF TmpDigitalOutputBits <> 0 THEN
                FOR Index := 0 TO 15 DO
                    IF (SHR(TmpDigitalOutputBits, TO_DINT(Index)) AND 1) <> 0 THEN
                        DigitalInputFlipCounts[Index] := DigitalInputFlipCounts[Index] + 1;
                    END_IF;
                END_FOR;
            END_IF;
        END_IF;
        DigitalInputBits_Prev := DigitalInputBits_Curr;
    END_IF;

    IF TRUE THEN//CheckAxisGroupReady
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

                // reMP overflow guard (see doc/ringbuf-bugs.md #4).
                IF (GVL.reMP_info_ridx.space() = 0) THEN
                    GVL.reMP_info_ridx.consumeTail();
                    GVL.ReMpDropCount := GVL.ReMpDropCount + 1;
                END_IF;
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
    END_IF;


    IF NOT AxisGroupReady THEN
        RETURN;
    END_IF;




    IF FALSE THEN///ProcessTaskPacket
        
        IF (PacketType = 'TASK') THEN
            ResponsePacketPointer := ADR(GVL.reMP_info_arr[GVL.reMP_info_ridx.getHead()]); // prepare for return data buffer

            MsgPakInfoClear(ResponsePacketPointer^);

            IF (CommandType = 'GA_EV') THEN
            	Index:=0;
            END_IF;

            GVL.minfo_buf_ridx.consumeTail();

            IF ShouldReturn = TRUE THEN // return ack/nak with id
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('id'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := CommandId);

                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('ack'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackBool(pStart := ResponsePacketPointer^.whead, bValue := CommandAck);

                ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 2;
                MsgPakInfoWrapup(ResponsePacketPointer^);
                GVL.reMP_info_ridx.pushHead();
            END_IF;
        END_IF;

    END_IF;

    
    IF TRUE THEN//ProcessFlyEventsAndIo

        TriggerActive := FALSE;
        FlyEventAvailableCount := 0;

        FOR Index := 0 TO TO_INT(FlyEventBufferSize - 1) DO
            TriggerActive := FALSE;
            TriggerErrorCode := 0;

            CASE FlyEventBuffer[Index].TriggerType OF
                FlyEventTriggerType.MovmentProgressTrigger:
                    IF (
                        CurrentMovementId > FlyEventBuffer[Index].TargetMovementId OR
                        (CurrentMovementId = FlyEventBuffer[Index].TargetMovementId AND CurrentMovementIdProgress >= FlyEventBuffer[Index].TargetMovementProgress)
                    ) THEN
                        TriggerActive := TRUE;
                        IF (FlyEventBuffer[Index].IoStageCount > 0) THEN
                            FlyEventBuffer[Index].IoStageIndex := 0;
                            FlyEventBuffer[Index].IoStageDelayCountdown := FlyEventBuffer[Index].IoStageDelay[0];
                            FlyEventBuffer[Index].TriggerType := FlyEventTriggerType.SequenceDelayWait;
                            IF (FlyEventBuffer[Index].IoStageDelayCountdown > 0) THEN
                                TriggerActive := FALSE;
                            END_IF;
                        END_IF;
                    ELSE
                        IF (FlyEventBuffer[Index].TimeToLiveMs > 0) THEN
                            FlyEventBuffer[Index].TimeToLiveMs := FlyEventBuffer[Index].TimeToLiveMs - BusPeriod;
                            IF (FlyEventBuffer[Index].TimeToLiveMs <= 0) THEN
                                TriggerActive := TRUE;
                                TriggerErrorCode := TRIGGER_TIMEOUT_ERR;
                            END_IF;
                        END_IF;
                    END_IF;

                FlyEventTriggerType.SequenceDelayWait:
                    IF (FlyEventBuffer[Index].IoStageIndex < FlyEventBuffer[Index].IoStageCount) THEN
                        FlyEventBuffer[Index].IoStageDelayCountdown := FlyEventBuffer[Index].IoStageDelayCountdown - BusPeriod;
                        IF (FlyEventBuffer[Index].IoStageDelayCountdown <= 0) THEN
                            TriggerActive := TRUE;
                        END_IF;
                    END_IF;

                FlyEventTriggerType.NOP:
                    FlyEventAvailableCount := FlyEventAvailableCount + 1;
            END_CASE;

            IF (TriggerActive = FALSE) THEN
                CONTINUE;
            END_IF;


            IF (TriggerErrorCode <> 0) THEN
                // reMP overflow guard (see doc/ringbuf-bugs.md #4).
                IF (GVL.reMP_info_ridx.space() = 0) THEN
                    GVL.reMP_info_ridx.consumeTail();
                    GVL.ReMpDropCount := GVL.ReMpDropCount + 1;
                END_IF;
                ResponsePacketPointer := ADR(GVL.reMP_info_arr[GVL.reMP_info_ridx.getHead()]); // prepare for return data buffer
                MsgPakInfoClear(ResponsePacketPointer^);
                ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 3;

                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('error_code'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TriggerErrorCode);

                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('src_id'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := FlyEventBuffer[Index].SourceCommandId);


                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('event_id'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := TO_LINT(FlyEventBuffer[Index].EventId));

                MsgPakInfoWrapup(ResponsePacketPointer^);
                GVL.reMP_info_ridx.pushHead();


                FlyEventBuffer[Index].ActionType := FlyEventActType.NOP;
                FlyEventBuffer[Index].TriggerType := FlyEventTriggerType.NOP;
            ELSE
                CASE FlyEventBuffer[Index].ActionType OF
                    FlyEventActType.PIN_OPERATION:
                        IF (FlyEventBuffer[Index].IoStageCount > 0) THEN
                            MotionOutputPin := FlyEventBuffer[Index].IoStagePinMask[FlyEventBuffer[Index].IoStageIndex];
                            StagePinState := FlyEventBuffer[Index].IoStagePinState[FlyEventBuffer[Index].IoStageIndex];
                        ELSE
                            MotionOutputPin := 0;
                            StagePinState := 0;
                        END_IF;

                        TmpDigitalOutputBits := DigitalOutputBits;
                        TmpDigitalOutputBits :=
                            (TmpDigitalOutputBits AND NOT (MotionOutputPin)) OR
                            ((StagePinState) AND (MotionOutputPin));
                        DigitalOutputBits := TmpDigitalOutputBits;

                        FlyEventBuffer[Index].IoStageIndex := FlyEventBuffer[Index].IoStageIndex + 1;
                        IF (FlyEventBuffer[Index].IoStageIndex < FlyEventBuffer[Index].IoStageCount) THEN
                            FlyEventBuffer[Index].IoStageDelayCountdown := FlyEventBuffer[Index].IoStageDelay[FlyEventBuffer[Index].IoStageIndex];
                            FlyEventBuffer[Index].TriggerType := FlyEventTriggerType.SequenceDelayWait;
                        ELSE
                            FlyEventBuffer[Index].ActionType := FlyEventActType.NOP;
                            FlyEventBuffer[Index].TriggerType := FlyEventTriggerType.NOP;
                        END_IF;

                        IoTriggerCount := IoTriggerCount + 1;

                    FlyEventActType.ACK_SRC_ID://send ack with source command id
                        // reMP overflow guard (see doc/ringbuf-bugs.md #4).
                        IF (GVL.reMP_info_ridx.space() = 0) THEN
                            GVL.reMP_info_ridx.consumeTail();
                            GVL.ReMpDropCount := GVL.ReMpDropCount + 1;
                        END_IF;

                        ResponsePacketPointer := ADR(GVL.reMP_info_arr[GVL.reMP_info_ridx.getHead()]); // prepare for return data buffer
                        MsgPakInfoClear(ResponsePacketPointer^);


                        ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count +3;//three elements: event_id, id, ack


                        ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('event_id'));
                        ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := TO_LINT(FlyEventBuffer[Index].EventId));



                        ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('id'));
                        ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := FlyEventBuffer[Index].SourceCommandId);

                        ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('ack'));
                        ResponsePacketPointer^.whead := MessagePackerFb.PackBool(pStart := ResponsePacketPointer^.whead, bValue := TRUE);

                        MsgPakInfoWrapup(ResponsePacketPointer^);
                        GVL.reMP_info_ridx.pushHead();

                        // IF (FlyEventBuffer[Index].SourceCommandId = CommandId) THEN
                        //     TriggerActive := TRUE;
                        // END_IF;
                        
                        FlyEventBuffer[Index].ActionType := FlyEventActType.NOP;
                        FlyEventBuffer[Index].TriggerType := FlyEventTriggerType.NOP;

                END_CASE;
            END_IF;

            IoTriggerInfoCount := IoTriggerInfoCount + 1;
        END_FOR;

        //DigitalOutputPointer := IoConfig_Globals.HECAT_1616.OutputData;//////////////////////////////////////////////
        OutputCHs[0] := TO_BYTE(DigitalOutputBits AND 16#FF);
        OutputCHs[1] := TO_BYTE(SHR(DigitalOutputBits,8) AND 16#FF);
		
		
		DBG_PIN:=0;
		IF((DigitalOutputBits AND 16#4000)  >0)THEN
			DBG_PIN:=1;
		END_IF
        MoveLinearAbsoluteFb(
            AxisGroup := AxisGroupManagerFb._agAxisGroup,
            Execute := 0
        );

	

        GroupWaitFb(
            AxisGroup := AxisGroupManagerFb._agAxisGroup,
            Execute := 0
        );

        IF (RetryMotionCommandCooldown > 0) THEN
            RetryMotionCommandCooldown := RetryMotionCommandCooldown - BusPeriod;
        END_IF;



        // B1 diagnostic: latch the rising edge of Execute (=ApplyCoordTransform)
        // BEFORE the FB call so we know exactly which scan kicked it off.
        IF (ApplyCoordTransform AND NOT PrevApplyCoordTransform) THEN
            GVL.DBG_SetCoordExecuteRiseMs := RuntimeMs;
            DbgSetCoordSeenBusy  := FALSE;
            DbgSetCoordSeenDone  := FALSE;
            DbgSetCoordSeenError := FALSE;
        END_IF;

        SetCoordTransformFb(
            AxisGroup := AxisGroupManagerFb._agAxisGroup,
            Execute := ApplyCoordTransform,
            CoordTransform := CoordTransform,
            CoordSystem := SMC_COORD_SYSTEM.MCS
        );

        // B1 diagnostic: catch the FB lifecycle even if Busy is asserted for
        // only a single scan. First-seen is what we care about; the FB clears
        // Done/Error on the next falling-edge of Execute.
        IF (SetCoordTransformFb.Busy AND NOT DbgSetCoordSeenBusy) THEN
            GVL.DBG_SetCoordBusyFirstMs := RuntimeMs;
            DbgSetCoordSeenBusy := TRUE;
        END_IF;
        IF (SetCoordTransformFb.Done AND NOT DbgSetCoordSeenDone) THEN
            GVL.DBG_SetCoordDoneMs := RuntimeMs;
            DbgSetCoordSeenDone := TRUE;
        END_IF;
        IF (SetCoordTransformFb.Error AND NOT DbgSetCoordSeenError) THEN
            GVL.DBG_SetCoordErrorMs := RuntimeMs;
            GVL.DBG_SetCoordLastErrorID := TO_DWORD(SetCoordTransformFb.ErrorID);
            DbgSetCoordSeenError := TRUE;
        END_IF;

        IF ApplyCoordTransform AND (SetCoordTransformFb.Busy OR SetCoordTransformFb.Done OR SetCoordTransformFb.Error) THEN
            ApplyCoordTransform := FALSE;
            GVL.DBG_SetCoordExecuteFallMs := RuntimeMs;
        END_IF;
        PrevApplyCoordTransform := ApplyCoordTransform;

        ReadMcsTransformFb(
            AxisGroup := AxisGroupManagerFb._agAxisGroup,
            Enable := TRUE,
            CoordSystem := SMC_COORD_SYSTEM.MCS,
            Valid => ,
            Busy => ,
            Error => ,
            ErrorID => ,
            CoordTransform =>
        );
    END_IF;
    

    reelpullmotor_fActPosition:=reelpullmotor.fActPosition;
    
    IF TRUE THEN//ProcessMotionPacket

        IF (RetryMotionCommandCooldown = 0 AND PacketType = 'M' AND GVL.minfo_buf_ridx.size() > 0 AND MotionBufferSize < MOTION_BUFFER_THRESHOLD) THEN
            ShouldReturn := FALSE;
            CommandAck := FALSE;

            // reMP overflow guard (see doc/ringbuf-bugs.md #4).
            IF (GVL.reMP_info_ridx.space() = 0) THEN
                GVL.reMP_info_ridx.consumeTail();
                GVL.ReMpDropCount := GVL.ReMpDropCount + 1;
            END_IF;
            ResponsePacketPointer := ADR(GVL.reMP_info_arr[GVL.reMP_info_ridx.getHead()]); // prepare for return data buffer
            MsgPakInfoClear(ResponsePacketPointer^);

            IF (GroupReadStatusFb.Error = TRUE OR GroupReadStatusFb.GroupErrorStop) THEN
                // Include an `err` string so the UI's NAK handler gets a readable
                // reason instead of the generic "nak (id=..., cmd=...)" fallback.
                ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 2;
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err'));
                IF (GroupReadStatusFb.GroupErrorStop) THEN
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('group_error_stop'));
                ELSE
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('group_read_status_error'));
                END_IF;
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('ERROR_CODE'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := GroupReadStatusFb.ErrorID);

                ShouldReturn := TRUE;
                CommandAck := FALSE;
                GVL.minfo_buf_ridx.consumeTail();

            ELSIF (CommandType = 'BLOCK_FOR_MOTION_STOP' OR CommandType = 'WAIT_FOR_MOTION_STOP') THEN
                // First scan we see this CommandId, latch start time and timeout.
                IF (CommandId <> BlockCommandId) THEN
                    BlockCommandId := CommandId;
                    BlockStartMs := RuntimeMs;
                    BlockTimeoutMs := MessageUnpackerFb.TryReadINT64('timeout_ms', 0);
                END_IF;

                IF (GroupReadPositionFb.MovementId = 0) THEN
                    ShouldReturn := TRUE;
                    CommandAck := TRUE;
                    GVL.minfo_buf_ridx.consumeTail();
                ELSIF (BlockTimeoutMs > 0 AND (RuntimeMs - BlockStartMs) >= BlockTimeoutMs) THEN
                    ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 1;
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err'));
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('block_timeout'));
                    ShouldReturn := TRUE;
                    CommandAck := FALSE;
                    GVL.minfo_buf_ridx.consumeTail();
                END_IF;

            ELSIF (CommandType = 'BLOCK_FOR_DIGITAL_INPUT') THEN
                InputPinMask := TO_UINT(MessageUnpackerFb.TryReadINT64('pin', 0));
                InputPinGroup := TO_UINT(MessageUnpackerFb.TryReadINT64('group', 0));
                InputPinState := TO_UINT(MessageUnpackerFb.TryReadINT64('state', 0));

                IF (CommandId <> BlockCommandId) THEN
                    BlockCommandId := CommandId;
                    BlockStartMs := RuntimeMs;
                    BlockTimeoutMs := MessageUnpackerFb.TryReadINT64('timeout_ms', 0);
                END_IF;

                IF DigitalInputPointer <> 0 AND ((DigitalInputPointer[InputPinGroup] AND InputPinMask) = (InputPinState AND InputPinMask)) THEN
                    ShouldReturn := TRUE;
                    CommandAck := TRUE;
                    GVL.minfo_buf_ridx.consumeTail();
                ELSIF (BlockTimeoutMs > 0 AND (RuntimeMs - BlockStartMs) >= BlockTimeoutMs) THEN
                    ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 1;
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err'));
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('block_timeout'));
                    ShouldReturn := TRUE;
                    CommandAck := FALSE;
                    GVL.minfo_buf_ridx.consumeTail();
                END_IF;
            ELSIF (CommandType = 'GET_DIGITAL_INPUT') THEN
                //InputPinGroup := TO_UINT(MessageUnpackerFb.TryReadINT64('group', 0));


                ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 1;

                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('state'));
                IF DigitalInputPointer <> 0 THEN
                    ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead,diValue:=DigitalInputPointer[0]OR  SHL( TO_UDINT( DigitalInputPointer[1]),8));
                ELSE
                    // DigitalInputPointer not wired (HECAT_1616 line in TrackDigitalInputFlips is commented out).
                    // Return 0 rather than dereferencing null.
                    ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := 0);
                END_IF;

                ShouldReturn := TRUE;
                CommandAck := TRUE;
                GVL.minfo_buf_ridx.consumeTail();
            ELSIF (CommandType = 'READ_LATEST_CMD_LOCATION') THEN	
                ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 6;

                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('X'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := TO_REAL(MotionPose.c.X));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('Y'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := TO_REAL(MotionPose.c.Y));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('Z'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := TO_REAL(MotionPose.c.Z));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('A'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := TO_REAL(MotionPose.c.A));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('B'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := TO_REAL(MotionPose.c.B));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('C'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := TO_REAL(MotionPose.c.C));

                ShouldReturn := TRUE;
                CommandAck := TRUE;
                GVL.minfo_buf_ridx.consumeTail();

            ELSIF (CommandType = 'G4') THEN
                ShouldReturn := TRUE;
                CommandAck := TRUE;
                G4HaltTime := MessageUnpackerFb.TryReadREAL('P', - 1);

                IF (G4HaltTime > 0) THEN
                    GroupWaitFb(
                        AxisGroup := AxisGroupManagerFb._agAxisGroup,
                        Execute := 1,
                        WaitTime := G4HaltTime
                    );

                    IF (GroupWaitFb.CommandAccepted) THEN
                        GVL.minfo_buf_ridx.consumeTail();
                        ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 1;

                        ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('movement_id'));
                        ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := TO_LINT(GroupWaitFb.MovementId));

                        ShouldReturn := TRUE;
                        CommandAck := TRUE;
                    ELSE
                        RetryMotionCommandCooldown := RETRY_COOLDOWN_G4_MS;
                    END_IF;
                ELSE
                    GVL.minfo_buf_ridx.consumeTail();

                    ShouldReturn := TRUE;
                    CommandAck := FALSE;
                END_IF;

            ELSIF (CommandType = 'SetCoord0') THEN
                CoordTransform.X := 0.0;
                CoordTransform.Y := 0.0;
                CoordTransform.Z := 0.0;
                CoordTransform.A := 0.0;
                CoordTransform.B := 0.0;
                CoordTransform.C := 0.0;
                ApplyCoordTransform := TRUE;
                GVL.CoordSystemConfigured := TRUE;

                ShouldReturn := TRUE;
                CommandAck := TRUE;
                GVL.minfo_buf_ridx.consumeTail();

            ELSIF (CommandType = 'SetCoord1') THEN
                CoordTransform.X := 0.0;
                CoordTransform.Y := 0.0;
                CoordTransform.Z := 0.0;
                CoordTransform.A := 60.0;
                CoordTransform.B := 0.0;
                CoordTransform.C := 0.0;
                ApplyCoordTransform := TRUE;
                GVL.CoordSystemConfigured := TRUE;
                GVL.DBG_SetCoord1AcceptMs := RuntimeMs;
                // Reset the lifecycle DBG_SetCoord* fields so an old reading
                // doesn't get mistaken for the new run.
                GVL.DBG_SetCoordExecuteRiseMs  := 0;
                GVL.DBG_SetCoordBusyFirstMs    := 0;
                GVL.DBG_SetCoordDoneMs         := 0;
                GVL.DBG_SetCoordErrorMs        := 0;
                GVL.DBG_SetCoordLastErrorID    := 0;
                GVL.DBG_SetCoordExecuteFallMs  := 0;

                ShouldReturn := TRUE;
                CommandAck := TRUE;
                GVL.minfo_buf_ridx.consumeTail();

            ELSIF (CommandType = 'RESET_DBG_INFO') THEN
                IoTriggerCount := 0;
                IoTriggerResetCount := 0;
                IoTriggerInfoCount := 0;
                IoCommandCount := 0;

                // Comm-stability counters reset (matches SYS/GET_DIAG payload)
                // so a UI dashboard can rebaseline cleanly. Doesn't reset
                // RuntimeMs / sm_scans (monotonic clocks) or last_ui_ping_ms
                // (live state, not a counter).
                GVL.ReMpDropCount               := 0;
                GVL.OverlenDropCount            := 0;
                GVL.SendStallDropCount          := 0;
                GVL.GroupNotReadyNakCount       := 0;
                GVL.MissingTypeFieldNakCount    := 0;
                GVL.CoordNotConfiguredNakCount  := 0;
                GVL.ProtocolVersionMismatchCount := 0;
                GVL.IdleResetCount              := 0;
                GVL.ReadErrorResetCount         := 0;
                GVL.ParserErrorResetCount       := 0;
                GVL.UiHeartbeatStaleCount       := 0;
                GVL.StateChangeEventCount       := 0;
                GVL.PingMaxGapMs                := 0;
                GVL.PrevPingRuntimeMs           := 0;

                ShouldReturn := TRUE;
                CommandAck := TRUE;
                GVL.minfo_buf_ridx.consumeTail();

            ELSIF (CommandType = 'ReelGo') THEN
                reelGoDistance := MessageUnpackerFb.TryReadREAL('Distance', reelGoDistance);
                reelGoVelocity := MessageUnpackerFb.TryReadREAL('F',        reelGoVelocity);
                reelGoAccel    := MessageUnpackerFb.TryReadREAL('ACC',      reelGoAccel);
                reelGoDecel    := MessageUnpackerFb.TryReadREAL('DEA',      reelGoDecel);
                reelGoJerk     := MessageUnpackerFb.TryReadREAL('JERK',     reelGoJerk);

                // Dispatch to whichever FB instance is not currently busy.
                // If both are busy (active + queued), drop the command.
                IF NOT reelMoveRelative.Busy THEN
                    reelGoRequest  := TRUE;
                ELSIF NOT reelMoveRelative2.Busy THEN
                    reelGoRequest2 := TRUE;
                END_IF;

                ShouldReturn := TRUE;
                CommandAck := TRUE;
                GVL.minfo_buf_ridx.consumeTail();

            ELSIF (CommandType = 'M4') THEN

				MotionTargetMovementId := TO_ULINT(MessageUnpackerFb.TryReadUINT64('motion_id', TO_ULINT(GroupReadStatusFb.LastAcceptedMovementId)));

				ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 1;

				ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('ev_buf_space'));

				ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := TO_LINT(FlyEventAvailableCount));



				IF (FlyEventAvailableCount > 3) THEN

					FlyEventTempData.ActionType := FlyEventActType.PIN_OPERATION;

					FlyEventTempData.TriggerType := FlyEventTriggerType.MovmentProgressTrigger;

					FlyEventTempData.SourceCommandId := CommandId;

					FlyEventTempData.PinGrp := 0;

					FlyEventTempData.ResetDelayMs := TO_INT(MessageUnpackerFb.TryReadINT64('reset_ms', 0));

					FlyEventTempData.TimeToLiveMs := TO_DINT(MessageUnpackerFb.TryReadINT64('ttl_ms', -1));

					FlyEventTempData.TargetMovementId := MotionTargetMovementId + TO_INT(MessageUnpackerFb.TryReadINT64('motion_id_offset', 0));

					FlyEventTempData.TargetMovementProgress := TO_LREAL(MessageUnpackerFb.TryReadREAL('motion_progress', 1));

					FlyEventTempData.EventId := TO_DINT(MessageUnpackerFb.TryReadINT64('event_id', 0));

					FlyEventTempData.IoStageCount := 0;

					FlyEventTempData.IoStageIndex := 0;

					FlyEventTempData.IoStageDelayCountdown := 0;

					FOR StageLoopIdx := 0 TO FlyEventStageLimit-1 DO

						FlyEventTempData.IoStageDelay[StageLoopIdx] := 0;

						FlyEventTempData.IoStagePinMask[StageLoopIdx] := 0;

						FlyEventTempData.IoStagePinState[StageLoopIdx] := 0;

					END_FOR;

					(* Parse pin_op_seq array: [delay0, pin0, state0, delay1, pin1, state1, ...] *)
					(* Use sequential reading with array unpacker for fast performance - no string operations *)
					IF MessageUnpackerFb.TryReadObject('pin_op_seq', ADR(fbPinOpSeqUnpacker)) THEN
						(* Read array header to get length *)
						fbPinOpSeqUnpacker.UnpackNext();
						
						(* Loop through array in groups of 3 (delay, pin, state) *)
						FOR PinOpSeqIdx := 0 TO (fbPinOpSeqUnpacker.uiLastCount / 3) - 1 DO
							IF FlyEventTempData.IoStageCount >= FlyEventStageLimit THEN
								EXIT; (* Reached maximum stage limit *)
							END_IF;
							
							(* Read delay value (signed integer) - sequential read *)
							fbPinOpSeqUnpacker.UnpackNext();
							StageDelayTemp := TO_INT(fbPinOpSeqUnpacker.diLastDINT);
							
							(* Read pin mask value (unsigned integer) - sequential read *)
							(* UnpackNext reads integers into diLastDINT; convert to ULINT for unsigned values *)
							fbPinOpSeqUnpacker.UnpackNext();
							(* Pin masks are bit patterns, inherently non-negative. Clamp negative
							   DINT values (e.g. client sent a signed negative int by mistake) to 0
							   so we don't wrap into a huge ULINT and trigger unintended outputs. *)
							IF fbPinOpSeqUnpacker.diLastDINT < 0 THEN
								StagePinMaskTemp := 0;
							ELSE
								StagePinMaskTemp := DINT_TO_ULINT(fbPinOpSeqUnpacker.diLastDINT);
							END_IF;

							(* Read pin state value (unsigned integer) - sequential read *)
							fbPinOpSeqUnpacker.UnpackNext();
							IF fbPinOpSeqUnpacker.diLastDINT < 0 THEN
								StagePinStateTemp := 0;
							ELSE
								StagePinStateTemp := DINT_TO_ULINT(fbPinOpSeqUnpacker.diLastDINT);
							END_IF;
							
							(* Only add stage if pin mask is non-zero *)
							IF StagePinMaskTemp <> 0 THEN
								FlyEventTempData.IoStageDelay[FlyEventTempData.IoStageCount] := StageDelayTemp;
								FlyEventTempData.IoStagePinMask[FlyEventTempData.IoStageCount] := StagePinMaskTemp;
								FlyEventTempData.IoStagePinState[FlyEventTempData.IoStageCount] := StagePinStateTemp;
								FlyEventTempData.IoStageCount := FlyEventTempData.IoStageCount + 1;
							END_IF;
						END_FOR;
					END_IF;



					StagePinMaskTemp := MessageUnpackerFb.TryReadUINT64('pin', 0);

					IF (StagePinMaskTemp <> 0 AND FlyEventTempData.IoStageCount < FlyEventStageLimit) THEN

						StagePinStateTemp := MessageUnpackerFb.TryReadUINT64('state', 0);

						StageDelayTemp := 0;

						FlyEventTempData.IoStageDelay[FlyEventTempData.IoStageCount] := StageDelayTemp;

						FlyEventTempData.IoStagePinMask[FlyEventTempData.IoStageCount] := StagePinMaskTemp;

						FlyEventTempData.IoStagePinState[FlyEventTempData.IoStageCount] := StagePinStateTemp;

						FlyEventTempData.IoStageCount := FlyEventTempData.IoStageCount + 1;

						IF (FlyEventTempData.ResetDelayMs > 0 AND FlyEventTempData.IoStageCount < FlyEventStageLimit) THEN

							FlyEventTempData.IoStageDelay[FlyEventTempData.IoStageCount] := FlyEventTempData.ResetDelayMs;

							FlyEventTempData.IoStagePinMask[FlyEventTempData.IoStageCount] := FlyEventTempData.IoStagePinMask[FlyEventTempData.IoStageCount - 1];

							FlyEventTempData.IoStagePinState[FlyEventTempData.IoStageCount] := ((NOT StagePinStateTemp) AND StagePinMaskTemp);

							FlyEventTempData.IoStageCount := FlyEventTempData.IoStageCount + 1;

						END_IF;

					END_IF;

					IF (FlyEventTempData.IoStageCount > 0 AND MotionTargetMovementId <> 0) THEN

						FOR Index := 0 TO TO_INT( FlyEventBufferSize - 1) DO

							IF (FlyEventBuffer[Index].TriggerType = 0) THEN

								FlyEventBuffer[Index] := FlyEventTempData;

								CommandAck := TRUE;

								EXIT;

							END_IF;

						END_FOR;

						IoCommandCount := IoCommandCount + 1;

						ShouldReturn := TRUE;

					END_IF;

					GVL.minfo_buf_ridx.consumeTail();

				END_IF;

			

ELSIF (CommandType = 'WAIT_FOR_TRIGGER_MOTION_PROGRESS') THEN
                IF (FlyEventAvailableCount > 3) THEN
					
                    MotionTargetMovementId := TO_ULINT(MessageUnpackerFb.TryReadINT64('motion_id', TO_LINT(GroupReadStatusFb.LastAcceptedMovementId)));




                    IF (MotionOutputPin <> 0 AND MotionTargetMovementId <> 0) THEN
                        FlyEventTempData.ActionType := FlyEventActType.ACK_SRC_ID;
                        FlyEventTempData.TriggerType := FlyEventTriggerType.MovmentProgressTrigger;
                        FlyEventTempData.TargetMovementId := MotionTargetMovementId + TO_INT(MessageUnpackerFb.TryReadINT64('motion_id_offset', 0));
                        FlyEventTempData.TargetMovementProgress := TO_LREAL(MessageUnpackerFb.TryReadREAL('motion_progress', 1));

                        FlyEventTempData.SourceCommandId := CommandId;


                        FlyEventTempData.EventId := TO_DINT(MessageUnpackerFb.TryReadINT64('event_id', 0));
                        FlyEventTempData.TimeToLiveMs := TO_DINT(MessageUnpackerFb.TryReadINT64('ttl_ms', -1));


                        FOR Index := 0 TO TO_INT(FlyEventBufferSize - 1) DO
                            IF (FlyEventBuffer[Index].TriggerType = 0) THEN
                                FlyEventBuffer[Index] := FlyEventTempData;
                                EXIT;
                            END_IF;
                        END_FOR;
                    END_IF;
                    GVL.minfo_buf_ridx.consumeTail();
                END_IF;


            ELSIF (CommandType = 'G1') THEN
                // Post-homing coord-system gate. Refuse G1 motion until the
                // operator has called SetCoord0 or SetCoord1 since the last
                // UnInited entry. Prevents machine-coord crashes when the UI
                // (or a test script) skips the SetCoord call after homing.
                IF (NOT GVL.CoordSystemConfigured) THEN
                    ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 1;
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err'));
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('coord_not_configured'));
                    GVL.CoordNotConfiguredNakCount := GVL.CoordNotConfiguredNakCount + 1;
                    ShouldReturn := TRUE;
                    CommandAck := FALSE;
                    GVL.minfo_buf_ridx.consumeTail();
                ELSE
                MotionPose.c.X := MessageUnpackerFb.TryReadREAL('X', MotionPose.c.X);
                MotionPose.c.Y := MessageUnpackerFb.TryReadREAL('Y', MotionPose.c.Y);
                MotionPose.c.Z := MessageUnpackerFb.TryReadREAL('Z', MotionPose.c.Z);
                MotionPoseTempReal := MessageUnpackerFb.TryReadREAL('A', G1_A_UNSET_SENTINEL);
                IF (MotionPoseTempReal <> G1_A_UNSET_SENTINEL) THEN
                    MotionPose.c.A := MotionPoseTempReal / 10;
                END_IF;

                LatestFeedRate := MessageUnpackerFb.TryReadREAL('F', LatestFeedRate);
                LatestAcceleration := MessageUnpackerFb.TryReadREAL('ACC', LatestAcceleration);
                LatestDeceleration := MessageUnpackerFb.TryReadREAL('DEA', LatestDeceleration);
                LatestJerk := MessageUnpackerFb.TryReadREAL('JERK', LatestJerk);
                LatestFactor := MessageUnpackerFb.TryReadREAL('FAC', LatestFactor);
                IF (LatestFactor > 1) THEN LatestFactor := 1; END_IF;
                IF (LatestFactor < 0.001) THEN LatestFactor := 0.001; END_IF;

                CornerTransition := MessageUnpackerFb.TryReadREAL('Cor', CornerTransition);
                TransitionParameter[0] := CornerTransition;
                G1AbortMode := MessageUnpackerFb.TryReadBool('abort', FALSE);




                MoveLinearAbsoluteFb(
                    AxisGroup := AxisGroupManagerFb._agAxisGroup,
                    Execute := 1,
                    Position := MotionPose,
                    Velocity := LatestFeedRate,
                    Acceleration := LatestAcceleration,
                    Deceleration := LatestDeceleration,
                    Jerk := LatestJerk,
                    CoordSystem := SMC_COORD_SYSTEM.WCS,
                    BufferMode := SEL(G1AbortMode,
                        SEL(CornerTransition >= 1, SM3_Common.MC_BUFFER_MODE.Buffered, SM3_Common.MC_BUFFER_MODE.BlendingNext),
                        SM3_Common.MC_BUFFER_MODE.Aborting),
                    TransitionMode := SM3_Robotics.MC_TRANSITION_MODE.TMCornerDistance,
                    TransitionParameter := TransitionParameter,
                    VelFactor := LatestFactor,
                    AccFactor := LatestFactor,
                    JerkFactor := LatestFactor,
                    TorqueFactor := 1,
                    Done => ,
                    Busy => ,
                    Active => ,
                    MovementId =>
                );

                IF (MoveLinearAbsoluteFb.CommandAccepted) THEN
                    GVL.minfo_buf_ridx.consumeTail();

                    ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 1;
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('movement_id'));
                    ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := TO_LINT(MoveLinearAbsoluteFb.MovementId));

                    ShouldReturn := TRUE;
                    CommandAck := TRUE;
                ELSE
                    RetryMotionCommandCooldown := RETRY_COOLDOWN_G1_MS;
                END_IF;
                END_IF; // GVL.CoordSystemConfigured gate

            ELSIF (CommandType = 'getDigitalInputFlipCount') THEN
                ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 2; // raw + fc

                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('raw'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(DigitalInputBits_Curr));

                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('fc'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackArrayHeader(pStart := ResponsePacketPointer^.whead, uiCount := 16);
                FOR StageLoopIdx := 0 TO 15 DO
                    ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(DigitalInputFlipCounts[StageLoopIdx]));
                END_FOR;

                ShouldReturn := TRUE;
                CommandAck := TRUE;
                GVL.minfo_buf_ridx.consumeTail();

            ELSE
                GVL.minfo_buf_ridx.consumeTail();
                ShouldReturn := TRUE;
            END_IF;

            IF ShouldReturn = TRUE AND CommandId <> 0 THEN
                ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 2;
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('id'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := CommandId);

                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('ack'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackBool(pStart := ResponsePacketPointer^.whead, bValue := CommandAck);

                MsgPakInfoWrapup(ResponsePacketPointer^);
                GVL.reMP_info_ridx.pushHead();
            END_IF;
        END_IF;
    END_IF;
    

// --- Reel motor cyclic driver ---
// Two FB instances so we can have one move active while another is
// queued in the axis buffer. SoftMotion rejects >1 movement per FB
// (SMC_MORE_THAN_ONE_MOVEMENT_PER_INSTANCE) so we dispatch to whichever
// FB is idle. BufferMode = Buffered: queued move starts when the active
// one finishes.
IF TRUE THEN
    reelMoveRelative(
        Axis         := reelpullmotor,
        Execute      := reelGoRequest,
        Distance     := reelGoDistance,
        Velocity     := reelGoVelocity,
        Acceleration := reelGoAccel,
        Deceleration := reelGoDecel,
        Jerk         := reelGoJerk,
        BufferMode   := MC_BUFFER_MODE.BlendingHigh
    );
    reelMoveRelative2(
        Axis         := reelpullmotor,
        Execute      := reelGoRequest2,
        Distance     := reelGoDistance,
        Velocity     := reelGoVelocity,
        Acceleration := reelGoAccel,
        Deceleration := reelGoDecel,
        Jerk         := reelGoJerk,
        BufferMode   := MC_BUFFER_MODE.BlendingHigh
    );

    // One-shot: clear both request flags every scan so each new 'ReelGo'
    // produces a fresh rising edge on whichever FB was selected.
    IF reelGoRequest  THEN reelGoRequest  := FALSE; END_IF
    IF reelGoRequest2 THEN reelGoRequest2 := FALSE; END_IF
END_IF;