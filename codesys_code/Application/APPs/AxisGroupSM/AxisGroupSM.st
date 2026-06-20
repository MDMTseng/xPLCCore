
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
        // Phase 2: scratch unpacker reused for the cam_table.{master,x,y,z}
        // sub-array reads inside the COORD1_BIND handler. One handler call
        // re-Inits this for each axis so we don't need 4 instances.
        fbCoord1CamUnpacker: FB_MpUnpacker;

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
        // Audit-fix: cap retries so a permanently-stuck reMP ring (e.g.,
        // UI hung but socket alive) eventually drops the pending event
        // instead of holding it forever. Comm task is 5ms; 600 scans = 3s.
        // Drop bumps GVL.PendingMoveDoneDropCount for telemetry.
        PendingMoveDoneRetryScans: UDINT := 0;
        PENDING_RETRY_MAX_SCANS: UDINT := 600;

        // PH#8: same defer-and-retry shape as PendingMoveDoneId, but for
        // ST_CHG. Edge stamps the "from" state into PendingStChgFromState
        // (-1 sentinel = nothing pending). Retry pump emits every scan
        // until the ring has space, pairing the latched "from" with the
        // current _eState as "to". Newest-edge-wins: rapid A->B->C while
        // ring is full collapses to one ST_CHG{from=B,to=C} -- UI uses
        // the global seq + runtime_ms gap to surface the skipped middle.
        PendingStChgFromState: DINT := -1;
        // Audit-fix: same retry cap as PendingMoveDoneRetryScans. Drops
        // bump GVL.PendingStChgDropCount.
        PendingStChgRetryScans: UDINT := 0;

        // PLC->UI HEARTBEAT push event. Emitted every HEARTBEAT_INTERVAL_MS
        // from UpdateRuntimeAndInputEvent so the UI can detect a dead PLC
        // socket without sending its own SYS/PING. Carries runtime_ms,
        // remp_drop_count, and (via Wrapup) the global seq -- so the UI
        // can also bound "how many events did I miss between heartbeats?".
        LastHeartbeatMs: LINT := 0;
        HEARTBEAT_INTERVAL_MS: LINT := 500;

        // PH#6: G1 command-id dedupe ring. UI-side TCP retry on jitter or
        // an operator double-clicking a button can re-send the same G1
        // packet with the same CommandId. Without dedupe we'd queue the
        // motion twice -- two physical moves for one logical request,
        // worst case axis collision. Track the last DUPE_RING_SIZE
        // accepted G1 (CommandId, MovementId) pairs; on G1 entry, scan
        // the ring -- on hit, replay the cached movement_id with
        // dedup:TRUE flag and skip the MoveLinearAbsoluteFb.Execute=1.
        // Block/poll commands (BLOCK_FOR_*) intentionally reuse one
        // CommandId across many scans, so dedupe is G1-only for now.
        DUPE_RING_SIZE: INT := 16;
        RecentCmdIds: ARRAY[0..15] OF DINT;
        RecentCmdMoveIds: ARRAY[0..15] OF ULINT;
        RecentCmdRingHead: INT := 0;
        DupeMatchIdx: INT := -1;
        DupeLoopIdx: INT := 0;


















    Index : INT;
    StageLoopIdx : INT;
    PinOpSeqIdx : INT; // Index for iterating through pin_op_seq array
    
    TempMovementId : SMC_Movement_Id;
    
    MessageBufferPointer : POINTER TO BYTE;


    TriggerActive : BOOL;
    TriggerErrorCode : INT;
    // Distance-trigger scratch for FlyEventTriggerType.DistanceTrigger.
    // Squared-distance compare avoids SQRT in the per-scan loop.
    DistDx : LREAL;
    DistDy : LREAL;
    DistDz : LREAL;
    DistSq : LREAL;
    DistThresholdSq : LREAL;
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
    // Optional 'trig' field on M4 packet -- selects between
    // MovmentProgressTrigger (default 20) and DistanceTrigger (120).
    FlyEventTriggerSelect : INT;
    MotionPoseTempReal : REAL;
    // Phase 3: G1 `frame` field. 0=coord0 (raw target, default/legacy),
    // 1=coord1 (target offset by GVL.Coord1OriginNow at MC call site).
    // MotionPose itself stays in user/coord0 frame so the "previous XYZ"
    // default-value semantics on subsequent G1s remain unchanged.
    G1FrameTag : INT;
    G1TargetPose : SM3_Robotics.SMC_POS_REF;
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

    // Coord1 cam-table lookup scratch (Phase 2). Reused each scan; values
    // meaningful only inside the lookup block immediately following the
    // CASE switch. Lo/Hi are the inclusive index range during binary
    // search; SegLo/SegHi are the two table indices straddling Pulse
    // after the search; Frac is the interpolation ratio in [0,1].
    CamLo, CamHi, CamMid : INT;
    CamSegLo, CamSegHi   : INT;
    CamFrac              : LREAL;

    // COORD1_BIND handler scratch (Phase 2). BindCamCount is the master[]
    // length advertised by the unpacker; BindCamIdx loops over samples;
    // BindPrevMaster validates strict ascending; BindAxisCount holds the
    // length of each subsequent x/y/z array for length-mismatch check;
    // BindHasTable / BindReject short-circuit the read path on first
    // validation failure to avoid further bogus writes.
    BindCamCount     : UINT;
    BindAxisCount    : UINT;
    BindCamIdx       : INT;
    BindPrevMaster   : DINT;
    BindCurMaster    : DINT;
    BindHasTable     : BOOL;
    BindReject       : BOOL;
    BindRejectReason : STRING(40);

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

    // Synthetic conveyor pulse generator (Phase 4 testing). Runs BEFORE
    // Coord1OriginNow recompute so this scan's offset uses the freshly
    // advanced pulse. Disabled by default; toggle via daemon force.
    IF GVL.ConveyorPulseSyntheticEnable THEN
        GVL.ConveyorPulseRaw := (GVL.ConveyorPulseRaw + GVL.ConveyorPulseSyntheticStep) AND 16#FFFF;
    END_IF;

    // Per-scan Coord1 origin recompute (conveyor tracking, Phase 1/2).
    // When bound, OriginNow drifts with the conveyor pulse so that a G1
    // target expressed in coord1 corresponds to the same physical point
    // on the belt regardless of belt advance.
    //   Linear mode: OriginNow[i] = RefXyz[i] + (Pulse-RefPulse)/Scale[i].
    //                Scale[i]=0 locks that axis to RefXyz (only the belt
    //                direction needs a non-zero scale).
    //   Table  mode: binary-search master[] for the bracketing segment,
    //                linearly interpolate x/y/z. Out-of-range pulse
    //                clamps to first or last sample (no extrapolation;
    //                cam profile is responsible for its own headroom).
    IF GVL.Coord1Bound THEN
        IF GVL.Coord1CamMode = GVL.COORD1_MODE_LINEAR THEN
                IF GVL.Coord1Scale[0] <> 0.0 THEN
                    GVL.Coord1OriginNow[0] := GVL.Coord1RefXyz[0]
                        + LREAL#1.0 * (GVL.ConveyorPulseRaw - GVL.Coord1RefPulse) / GVL.Coord1Scale[0];
                ELSE
                    GVL.Coord1OriginNow[0] := GVL.Coord1RefXyz[0];
                END_IF
                IF GVL.Coord1Scale[1] <> 0.0 THEN
                    GVL.Coord1OriginNow[1] := GVL.Coord1RefXyz[1]
                        + LREAL#1.0 * (GVL.ConveyorPulseRaw - GVL.Coord1RefPulse) / GVL.Coord1Scale[1];
                ELSE
                    GVL.Coord1OriginNow[1] := GVL.Coord1RefXyz[1];
                END_IF
                IF GVL.Coord1Scale[2] <> 0.0 THEN
                    GVL.Coord1OriginNow[2] := GVL.Coord1RefXyz[2]
                        + LREAL#1.0 * (GVL.ConveyorPulseRaw - GVL.Coord1RefPulse) / GVL.Coord1Scale[2];
                ELSE
                    GVL.Coord1OriginNow[2] := GVL.Coord1RefXyz[2];
                END_IF

        ELSIF GVL.Coord1CamMode = GVL.COORD1_MODE_TABLE THEN
                IF GVL.Coord1CamCount <= 0 THEN
                    // Defensive: a Table-mode bind without samples shouldn't
                    // happen (COORD1_BIND handler enforces count>=2), but if
                    // it does, fall back to RefXyz.
                    GVL.Coord1OriginNow[0] := GVL.Coord1RefXyz[0];
                    GVL.Coord1OriginNow[1] := GVL.Coord1RefXyz[1];
                    GVL.Coord1OriginNow[2] := GVL.Coord1RefXyz[2];
                ELSIF GVL.ConveyorPulseRaw <= GVL.Coord1CamMaster[0] THEN
                    // Clamp to first sample.
                    GVL.Coord1OriginNow[0] := GVL.Coord1RefXyz[0] + GVL.Coord1CamX[0];
                    GVL.Coord1OriginNow[1] := GVL.Coord1RefXyz[1] + GVL.Coord1CamY[0];
                    GVL.Coord1OriginNow[2] := GVL.Coord1RefXyz[2] + GVL.Coord1CamZ[0];
                ELSIF GVL.ConveyorPulseRaw >= GVL.Coord1CamMaster[GVL.Coord1CamCount - 1] THEN
                    // Clamp to last sample.
                    GVL.Coord1OriginNow[0] := GVL.Coord1RefXyz[0] + GVL.Coord1CamX[GVL.Coord1CamCount - 1];
                    GVL.Coord1OriginNow[1] := GVL.Coord1RefXyz[1] + GVL.Coord1CamY[GVL.Coord1CamCount - 1];
                    GVL.Coord1OriginNow[2] := GVL.Coord1RefXyz[2] + GVL.Coord1CamZ[GVL.Coord1CamCount - 1];
                ELSE
                    // Binary search: find largest CamLo with master[Lo] <= Pulse.
                    // Invariant: master[Lo] <= Pulse < master[Hi]. Loop ends with
                    // Hi = Lo + 1, the bracketing segment.
                    CamLo := 0;
                    CamHi := GVL.Coord1CamCount - 1;
                    WHILE (CamHi - CamLo) > 1 DO
                        CamMid := (CamLo + CamHi) / 2;
                        IF GVL.Coord1CamMaster[CamMid] <= GVL.ConveyorPulseRaw THEN
                            CamLo := CamMid;
                        ELSE
                            CamHi := CamMid;
                        END_IF
                    END_WHILE
                    CamSegLo := CamLo;
                    CamSegHi := CamLo + 1;
                    // Frac in [0,1]; denom non-zero because master[] is strictly
                    // ascending (validated at bind time).
                    CamFrac := LREAL#1.0 * (GVL.ConveyorPulseRaw - GVL.Coord1CamMaster[CamSegLo])
                             / (GVL.Coord1CamMaster[CamSegHi] - GVL.Coord1CamMaster[CamSegLo]);
                    GVL.Coord1OriginNow[0] := GVL.Coord1RefXyz[0]
                        + GVL.Coord1CamX[CamSegLo] + CamFrac * (GVL.Coord1CamX[CamSegHi] - GVL.Coord1CamX[CamSegLo]);
                    GVL.Coord1OriginNow[1] := GVL.Coord1RefXyz[1]
                        + GVL.Coord1CamY[CamSegLo] + CamFrac * (GVL.Coord1CamY[CamSegHi] - GVL.Coord1CamY[CamSegLo]);
                    GVL.Coord1OriginNow[2] := GVL.Coord1RefXyz[2]
                        + GVL.Coord1CamZ[CamSegLo] + CamFrac * (GVL.Coord1CamZ[CamSegHi] - GVL.Coord1CamZ[CamSegLo]);
                END_IF
        END_IF
    END_IF

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
               AND MotionBufferSize > 0
               AND GVL.LastUiPingMs > 0
               AND (RuntimeMs - GVL.LastUiPingMs) > GVL.UI_HEARTBEAT_TIMEOUT_MS) THEN
            // W1 A3 (PLC supervisor): UI went silent WHILE motion is in flight.
            // Pre-fix the trigger was just (Ready AND age > timeout), which
            // fired on idle Ready when the UI was simply backgrounded
            // (Electron throttles its tick loop) -- causing spurious Error
            // transitions on a perfectly healthy system. The unsafe scenario
            // is "client died with motion still queued"; the safe scenario
            // is "client is quiet because the operator went to lunch".
            // Gate on MotionBufferSize > 0 to distinguish them. Combined with
            // the drain-loop LastUiPingMs refresh above, a steady G1 stream
            // self-keepalives -- the watchdog only fires when the client
            // really has gone silent during in-flight motion.
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

        // Any inbound packet -- not just SYS/PING -- proves the client is
        // alive. Pre-fix only PING refreshed LastUiPingMs, so a UI sending
        // a steady G1 stream (no PING) still tripped the A3 supervisor
        // mid-motion. Refresh on every drain-loop entry so the watchdog
        // tracks "client is talking" rather than "client is pinging".
        // The PING handler's own refresh below is now redundant but kept
        // alongside the PingMaxGapMs / UiPingCount stats it owns.
        GVL.LastUiPingMs := RuntimeMs;

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
                GVL.ReMpDropCount_Reply := GVL.ReMpDropCount_Reply + 1;
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
                GVL.ReMpDropCount_Reply := GVL.ReMpDropCount_Reply + 1;
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
            ELSIF (CommandType = 'VERSION') THEN
                // Build provenance: lets an external tool verify the running
                // PLC matches a repo commit without IDE assistance. Stamped by
                // codesys_scripts/jobs/stamp_build_info.py before import_all.
                ShouldReturn := TRUE;
                CommandAck := TRUE;
                ResponsePacketPointer^.ele_count := 3;
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('git_sha'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR(GVL.BUILD_GIT_SHA));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('build_ts_ms'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := GVL.BUILD_TS_MS);
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('runtime_ms'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := RuntimeMs);
            ELSIF (CommandType = 'GET_DIAG') THEN
                // Generic comm/robustness counters dump. UI dashboard polls this
                // (e.g. once per second) to chart drop/NAK/reset rates and the
                // PING gap-jitter envelope. Read-only; doesn't perturb FSM.
                // Counters resettable via SYS/RESET_DBG_INFO (see existing handler).
                ShouldReturn := TRUE;
                CommandAck := TRUE;
                // GET_DIAG fields extended 2026-06-17 to be a TCP-side
                // superset of the daemon-side counter dump the fuzz tests
                // used to call via oapp.read_value. Lets fuzz/soak tests run
                // without the CODESYS RPC daemon being alive -- daemon dies
                // mid-test no longer breaks the test. New fields:
                //   self_reentry, pending_stchg_drop, pending_movedone_drop,
                //   remp_drop_reply, remp_drop_trig, dupe_cmd,
                //   io_cmd_count, io_trig_count, flyevent_avail
                ResponsePacketPointer^.ele_count := 27;
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
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('self_reentry'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.SelfReentryCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('pending_stchg_drop'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.PendingStChgDropCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('pending_movedone_drop'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.PendingMoveDoneDropCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('remp_drop_reply'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.ReMpDropCount_Reply));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('remp_drop_trig'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.ReMpDropCount_TriggerEvent));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('dupe_cmd'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.DupeCommandCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('io_cmd_count'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(IoCommandCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('io_trig_count'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(IoTriggerCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('flyevent_avail'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(FlyEventAvailableCount));
            ELSIF (CommandType = 'COORD1_BIND') THEN
                // Bind / re-bind the coord1 tracking frame to the conveyor
                // encoder. Linear mode (no cam_table): origin drifts at a
                // constant rate per pulse via Scale[]. Table mode (cam_table
                // present): origin follows a sampled master->offset curve,
                // linearly interpolated between samples. Reject if the
                // motion buffer is non-empty so the active move's coord
                // frame doesn't change mid-flight.
                ShouldReturn := TRUE;
                BindReject := FALSE;
                BindRejectReason := '';

                IF MotionBufferSize > 0 THEN
                    BindReject := TRUE;
                    BindRejectReason := 'coord_bind_busy';
                    GVL.Coord1BindBusyNakCount := GVL.Coord1BindBusyNakCount + 1;
                END_IF

                IF NOT BindReject THEN
                    // Read top-level scalars (path-based, defaulting to 0).
                    GVL.Coord1RefPulse   := TO_DINT(MessageUnpackerFb.TryReadINT64('ref_pulse', 0));
                    GVL.Coord1RefXyz[0]  := TO_LREAL(MessageUnpackerFb.TryReadREAL('ref_xyz[0]', 0.0));
                    GVL.Coord1RefXyz[1]  := TO_LREAL(MessageUnpackerFb.TryReadREAL('ref_xyz[1]', 0.0));
                    GVL.Coord1RefXyz[2]  := TO_LREAL(MessageUnpackerFb.TryReadREAL('ref_xyz[2]', 0.0));
                    GVL.Coord1Scale[0]   := TO_LREAL(MessageUnpackerFb.TryReadREAL('scale[0]', 0.0));
                    GVL.Coord1Scale[1]   := TO_LREAL(MessageUnpackerFb.TryReadREAL('scale[1]', 0.0));
                    GVL.Coord1Scale[2]   := TO_LREAL(MessageUnpackerFb.TryReadREAL('scale[2]', 0.0));

                    // Optional cam_table.master[] probe -> Table mode.
                    BindHasTable := MessageUnpackerFb.TryReadObject('cam_table.master', ADR(fbCoord1CamUnpacker));
                END_IF

                IF NOT BindReject AND BindHasTable THEN
                    fbCoord1CamUnpacker.UnpackNext();
                    BindCamCount := fbCoord1CamUnpacker.uiLastCount;
                    IF BindCamCount < 2 OR BindCamCount > TO_UINT(GVL.COORD1_CAM_MAX_POINTS) THEN
                        BindReject := TRUE;
                        BindRejectReason := 'coord_bind_bad_table';
                        GVL.Coord1BadTableNakCount := GVL.Coord1BadTableNakCount + 1;
                    END_IF
                    IF NOT BindReject THEN
                        // Read master[]; validate strictly ascending.
                        BindPrevMaster := -16#7FFFFFFF;
                        FOR BindCamIdx := 0 TO TO_INT(BindCamCount) - 1 DO
                            fbCoord1CamUnpacker.UnpackNext();
                            BindCurMaster := fbCoord1CamUnpacker.diLastDINT;
                            IF BindCamIdx > 0 AND BindCurMaster <= BindPrevMaster THEN
                                BindReject := TRUE;
                                BindRejectReason := 'coord_bind_bad_table';
                                GVL.Coord1BadTableNakCount := GVL.Coord1BadTableNakCount + 1;
                                EXIT;
                            END_IF
                            GVL.Coord1CamMaster[BindCamIdx] := BindCurMaster;
                            BindPrevMaster := BindCurMaster;
                        END_FOR
                    END_IF
                    // Read x[]
                    IF NOT BindReject THEN
                        IF NOT MessageUnpackerFb.TryReadObject('cam_table.x', ADR(fbCoord1CamUnpacker)) THEN
                            BindReject := TRUE;
                            BindRejectReason := 'coord_bind_bad_table';
                            GVL.Coord1BadTableNakCount := GVL.Coord1BadTableNakCount + 1;
                        ELSE
                            fbCoord1CamUnpacker.UnpackNext();
                            BindAxisCount := fbCoord1CamUnpacker.uiLastCount;
                            IF BindAxisCount <> BindCamCount THEN
                                BindReject := TRUE;
                                BindRejectReason := 'coord_bind_table_length_mismatch';
                                GVL.Coord1BadTableNakCount := GVL.Coord1BadTableNakCount + 1;
                            ELSE
                                FOR BindCamIdx := 0 TO TO_INT(BindCamCount) - 1 DO
                                    fbCoord1CamUnpacker.UnpackNext();
                                    GVL.Coord1CamX[BindCamIdx] := fbCoord1CamUnpacker.rLastREAL;
                                END_FOR
                            END_IF
                        END_IF
                    END_IF
                    // Read y[]
                    IF NOT BindReject THEN
                        IF NOT MessageUnpackerFb.TryReadObject('cam_table.y', ADR(fbCoord1CamUnpacker)) THEN
                            BindReject := TRUE;
                            BindRejectReason := 'coord_bind_bad_table';
                            GVL.Coord1BadTableNakCount := GVL.Coord1BadTableNakCount + 1;
                        ELSE
                            fbCoord1CamUnpacker.UnpackNext();
                            BindAxisCount := fbCoord1CamUnpacker.uiLastCount;
                            IF BindAxisCount <> BindCamCount THEN
                                BindReject := TRUE;
                                BindRejectReason := 'coord_bind_table_length_mismatch';
                                GVL.Coord1BadTableNakCount := GVL.Coord1BadTableNakCount + 1;
                            ELSE
                                FOR BindCamIdx := 0 TO TO_INT(BindCamCount) - 1 DO
                                    fbCoord1CamUnpacker.UnpackNext();
                                    GVL.Coord1CamY[BindCamIdx] := fbCoord1CamUnpacker.rLastREAL;
                                END_FOR
                            END_IF
                        END_IF
                    END_IF
                    // Read z[]
                    IF NOT BindReject THEN
                        IF NOT MessageUnpackerFb.TryReadObject('cam_table.z', ADR(fbCoord1CamUnpacker)) THEN
                            BindReject := TRUE;
                            BindRejectReason := 'coord_bind_bad_table';
                            GVL.Coord1BadTableNakCount := GVL.Coord1BadTableNakCount + 1;
                        ELSE
                            fbCoord1CamUnpacker.UnpackNext();
                            BindAxisCount := fbCoord1CamUnpacker.uiLastCount;
                            IF BindAxisCount <> BindCamCount THEN
                                BindReject := TRUE;
                                BindRejectReason := 'coord_bind_table_length_mismatch';
                                GVL.Coord1BadTableNakCount := GVL.Coord1BadTableNakCount + 1;
                            ELSE
                                FOR BindCamIdx := 0 TO TO_INT(BindCamCount) - 1 DO
                                    fbCoord1CamUnpacker.UnpackNext();
                                    GVL.Coord1CamZ[BindCamIdx] := fbCoord1CamUnpacker.rLastREAL;
                                END_FOR
                            END_IF
                        END_IF
                    END_IF
                END_IF

                IF BindReject THEN
                    CommandAck := FALSE;
                    ResponsePacketPointer^.ele_count := 1;
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err'));
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR(BindRejectReason));
                ELSE
                    // Commit: flip mode + bound atomically (single scan; the
                    // cyclic compute at top of scan won't observe a half-set
                    // state because we're inside the SYS drain, which runs
                    // after the per-scan recompute).
                    IF BindHasTable THEN
                        GVL.Coord1CamMode  := GVL.COORD1_MODE_TABLE;
                        GVL.Coord1CamCount := TO_INT(BindCamCount);
                    ELSE
                        GVL.Coord1CamMode  := GVL.COORD1_MODE_LINEAR;
                        GVL.Coord1CamCount := 0;
                    END_IF
                    GVL.Coord1Bound       := TRUE;
                    GVL.Coord1BindCount   := GVL.Coord1BindCount + 1;
                    CommandAck := TRUE;
                    ResponsePacketPointer^.ele_count := 2;
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('mode'));
                    IF BindHasTable THEN
                        ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('table'));
                    ELSE
                        ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('linear'));
                    END_IF
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('n'));
                    ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.Coord1CamCount));
                END_IF
            ELSIF (CommandType = 'GET_COORD1_DEBUG') THEN
                // Coord1 bind/tracking inspector (Phase 1). Echoes the raw
                // conveyor pulse, bind state, ref/scale, and the per-scan
                // drifting OriginNow used by frame=coord1 G1. Read-only.
                ShouldReturn := TRUE;
                CommandAck := TRUE;
                ResponsePacketPointer^.ele_count := 19;
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('bound'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackBool(pStart := ResponsePacketPointer^.whead, bValue := GVL.Coord1Bound);
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('mode'));
                IF GVL.Coord1CamMode = GVL.COORD1_MODE_TABLE THEN
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('table'));
                ELSE
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('linear'));
                END_IF
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('cam_n'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.Coord1CamCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('cam_first'));
                IF GVL.Coord1CamCount > 0 THEN
                    ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := GVL.Coord1CamMaster[0]);
                ELSE
                    ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := 0);
                END_IF
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('cam_last'));
                IF GVL.Coord1CamCount > 0 THEN
                    ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := GVL.Coord1CamMaster[GVL.Coord1CamCount - 1]);
                ELSE
                    ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := 0);
                END_IF
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('pulse_raw'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := GVL.ConveyorPulseRaw);
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('ref_pulse'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := GVL.Coord1RefPulse);
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('ref_x'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := TO_REAL(GVL.Coord1RefXyz[0]));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('ref_y'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := TO_REAL(GVL.Coord1RefXyz[1]));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('ref_z'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := TO_REAL(GVL.Coord1RefXyz[2]));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('scale_x'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := TO_REAL(GVL.Coord1Scale[0]));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('scale_y'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := TO_REAL(GVL.Coord1Scale[1]));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('scale_z'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := TO_REAL(GVL.Coord1Scale[2]));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('origin_x'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := TO_REAL(GVL.Coord1OriginNow[0]));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('origin_y'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := TO_REAL(GVL.Coord1OriginNow[1]));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('origin_z'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := TO_REAL(GVL.Coord1OriginNow[2]));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('bind_count'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.Coord1BindCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('bind_busy_nak'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.Coord1BindBusyNakCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('bad_table_nak'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.Coord1BadTableNakCount));
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
                    GVL.ReMpDropCount_Reply := GVL.ReMpDropCount_Reply + 1;
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
                GVL.ReMpDropCount_Reply := GVL.ReMpDropCount_Reply + 1;
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
                    GVL.ReMpDropCount_Reply := GVL.ReMpDropCount_Reply + 1;
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