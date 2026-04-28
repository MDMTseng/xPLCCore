
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
        // A-axis Kin_CAxis wrap workaround: SpiderR's chained kinematic
        // wraps c.A to +/-180 deg structurally, but the UI commands
        // +/-360 deg. We divide by this constant here and multiply by
        // the same constant on the SM_Drive_GenericDSP402 axis-side
        // scaling (CODESYS GUI), so the wrap is dodged round-trip.
        // Both ends MUST match. See doc/coupling_invariants.md
        // "A-axis /10 scaling" before changing.
        A_AXIS_KIN_WRAP_SCALE   : LREAL := 10;
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
        // Drop-newest gating flag for reMP_info_ridx emission paths whose
        // pack-into-slot block spans many lines and many branches (the SYS
        // and motion handlers below). When the ring is full the producer
        // routes ResponsePacketPointer to GVL.reMP_info_scratch instead of
        // slot[head] (writing slot[head] when full would corrupt the slot
        // the Comm task is about to read), and pushHead is skipped. Short
        // emission blocks use a direct IF (space>0) wrap instead.
        SlotAvailable: BOOL := FALSE;

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
        // PH#5: edge detect for MOVE_DONE is one-shot
        // (PrevMotionBufferSizeForEvent transitions 1->0 next scan), so a
        // ring-full drop loses the event entirely and the UI has to wait
        // up to 1s for the GET_MACHINE_STATE poll. Defer the emission via
        // PendingMoveDoneId: edge sets it, every subsequent scan retries
        // until the push lands. Stays >0 only between detection and emit.
        PendingMoveDoneId: ULINT := 0;

        // PLC->UI HEARTBEAT push event. Emitted every HEARTBEAT_INTERVAL_MS
        // from UpdateRuntimeAndInputEvent so the UI can detect a dead PLC
        // socket without sending its own SYS/PING. Carries runtime_ms,
        // remp_drop_count, and (via Wrapup) the global seq -- so the UI
        // can also bound "how many events did I miss between heartbeats?".
        LastHeartbeatMs: LINT := 0;
        HEARTBEAT_INTERVAL_MS: LINT := 500;


















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
    // PH#3: TimeToLiveMs is DINT; raw msgpack value is INT64. Cast via
    // TO_DINT silently wraps values >MAX_DINT to negative, masking the
    // request as "no expiry" (-1) and producing a stuck FlyEvent. Stage
    // through LINT and clamp before the cast.
    FlyEventTtlRaw : LINT;
    MotionPoseTempReal : REAL;
    PrevMotionStateId : SMC_Movement_Id; // For MotionStateString change detection
    PrevMotionStateProgress : REAL := -1.0; // Force initial update
    G1AbortMode : BOOL; // TRUE = BufferMode.Aborting, interrupts current move


    
    G4HaltTime: REAL;
    InputPinMask: UINT;
    InputPinGroup: UINT;
    InputPinState: UINT;
    
    MotionTargetMovementId: SMC_Movement_Id;
    // Scratch for safely combining MotionTargetMovementId (ULINT) with the
    // signed motion_id_offset payload field. DINT (32-bit signed) covers any
    // realistic offset; the previous TO_INT cast wrapped offsets >32767 to
    // negative and silently rolled TargetMovementId backwards.
    MotionIdOffsetTemp: DINT;
    
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


    UpdateRuntimeAndInputEvent();


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


    UpdateMotionProgress();


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
            // Drop-newest on overflow (see UpdateMotionProgress). The
            // minfo_buf consumeTail and counter still run so a full reply
            // ring doesn't infinitely re-NAK the same packet.
            IF (GVL.reMP_info_ridx.space() > 0) THEN
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
            ELSE
                GVL.ReMpDropCount := GVL.ReMpDropCount + 1;
            END_IF;

            GVL.minfo_buf_ridx.consumeTail();
            GVL.ProtocolVersionMismatchCount := GVL.ProtocolVersionMismatchCount + 1;

            // Neutralise downstream dispatch in case loop exits next.
            PacketType := '';
            CommandType := '';
            ResponsePacketPointer := 0;
            SysDrainIter := SysDrainIter + 1;

        ELSIF (PacketType = 'SYS') THEN
            // reMP overflow: drop-newest. Producer must NOT consumeTail
            // (Comm task is sole consumer; two consumers race on _tail).
            // The SYS handler block below has many branches that all pack
            // into ResponsePacketPointer^; route to GVL.reMP_info_scratch
            // when full so the inline pack code stays unchanged. The
            // pushHead at the bottom of this branch is gated on
            // SlotAvailable.
            IF (GVL.reMP_info_ridx.space() > 0) THEN
                SlotAvailable := TRUE;
                ResponsePacketPointer := ADR(GVL.reMP_info_arr[GVL.reMP_info_ridx.getHead()]);
            ELSE
                SlotAvailable := FALSE;
                GVL.ReMpDropCount := GVL.ReMpDropCount + 1;
                ResponsePacketPointer := ADR(GVL.reMP_info_scratch);
            END_IF;

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
                ResponsePacketPointer^.ele_count := 12;
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
                // axes_labels : self-describing axis order, same indexing as
                // axes_err_id / axes_err_mask bits 0..3. UI consumes this
                // instead of hardcoding labels, collapsing the four-site
                // axes_err_id ordering coupling (PLC pack / GVL / mask bits /
                // UI labels) down to a single source of truth on the PLC.
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('axes_labels'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackArrayHeader(pStart := ResponsePacketPointer^.whead, uiCount := 4);
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('EAxis0 (arm1)'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('EAxis1 (arm2)'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('EAxis2 (arm3)'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('reelpullmotor'));
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
                ResponsePacketPointer^.ele_count := 18;
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('remp_overflow_drop'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.ReMpOverflowDropCount));
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
                // Drop-newest: only commit if the real ring slot was reserved.
                // If SlotAvailable=FALSE we packed into the scratch slot and
                // must NOT pushHead (would corrupt the slot a consumer is
                // currently reading). See GVL.reMP_info_scratch note.
                IF SlotAvailable THEN
                    GVL.reMP_info_ridx.pushHead();
                ELSE
                    GVL.ReMpDropCount := GVL.ReMpDropCount + 1;
                END_IF;
            END_IF;
            SysDrainIter := SysDrainIter + 1;

        ELSIF (PacketType <> 'M') THEN
            // Tail is neither SYS nor 'M'. Most likely a caller forgot to set
            // type:'M' on a motion command (closed B1 ghost: SetCoord1 sent
            // as {cmd:'SetCoord1'} with no type sat at the tail forever,
            // blocked PINGs, supervisor tripped at 5s). NAK inline so a
            // future caller fails fast instead of "stalling" -- and so any
            // SYS packets queued behind it can drain in this same loop.
            // Drop-newest: skip the NAK entirely if the reply ring is full
            // rather than overwrite a slot the Comm-task consumer is reading.
            IF (GVL.reMP_info_ridx.space() > 0) THEN
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
            ELSE
                GVL.ReMpDropCount := GVL.ReMpDropCount + 1;
            END_IF;

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
                // Drop-newest: skip the NAK entirely if the reply ring is full.
                IF (GVL.reMP_info_ridx.space() > 0) THEN
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
                ELSE
                    GVL.ReMpDropCount := GVL.ReMpDropCount + 1;
                END_IF;

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

    CheckAxisGroupReady();


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

    
    ProcessFlyEventsAndIo();
    

    reelpullmotor_fActPosition:=reelpullmotor.fActPosition;
    
    ProcessMotionPacket();
    

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