
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
        // Both ends MUST match. See doc/1-concepts/coupling_invariants.md
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
        // Phase 4 step 3: emergency stop the arm when COORD1 tracking
        // raises a fault. Fired on EV_ERROR rising edge alongside the
        // event push so the arm doesn't keep chasing a stale belt
        // target while the host is recovering.
        GroupStopFb: SM3_Robotics.MC_GroupStop;
        GroupStopExecute: BOOL := FALSE;

        (* Phase 4: conveyor encoder bridge. ConveyorEncoderAxis is an
           SMC_VirtualDrive added to the SoftMotion General Axis Pool by
           codesys_scripts/jobs/_oneoff/add_conveyor_axis.py. We power it
           up once and write GVL.ConveyorPulseRaw (scaled by mm/pulse)
           into fSetPosition every scan, so MC_TrackConveyorBelt can use
           it as a real SM3 axis reference even before the hardware
           encoder exists. *)
        conveyorPower : MC_Power;
        Coord1PulseToMm : LREAL := 0.01;   // calibration: 1 pulse = 0.01 mm

        (* Phase 4 step 2: MC_TrackConveyorBelt links PCS_1 to the
           conveyor virtual axis. When Coord1Bound rises, the kernel
           starts continuously updating PCS_1 origin in WCS as
              PCS_1_origin = Coord1BeltOriginRef + ConveyorEncoderAxis.fActPosition * belt_direction
           and any MC_MoveLinearAbsolute with CoordSystem := PCS_1 tracks
           the belt automatically (the synchronized handover the host
           wanted). belt direction = PCS_1 +X by SM3 convention; rotary
           tables live in MC_TrackConveyorBelt's sibling MC_TrackRotaryTable
           (a future Phase). *)
        trackConveyorBeltFb : MC_TrackConveyorBelt;
        Coord1BeltOriginRef : MC_COORD_REF;

        (* Phase 4 step 2 diagnostic: read what the kernel currently
           thinks PCS_1's origin is in WCS. This is the source of truth
           for what G1 frame=1 actually targets, and lets us verify
           MC_TrackConveyorBelt is doing its job by diffing it against
           GVL.Coord1OriginNow (the host-side prediction). *)
        ReadPcs1TransformFb : MC_ReadCoordinateTransform;
        Pcs1KernelTransform : MC_COORD_REF;

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
        // the Comm task is about to read), and PushHead is skipped. Short
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
    // Phase 3: G1 `frame` field. 0=coord0 (default/legacy, MoveLinearAbsolute
    // CoordSystem := WCS), 1=coord1 (Phase 4 step 2: CoordSystem := PCS_1;
    // MC_TrackConveyorBelt keeps PCS_1 synced to the belt). Tag is also
    // gated by GVL.Coord1Bound at G1 dispatch time (frame=1 without bind
    // NAKs with 'coord1_not_bound').
    G1FrameTag : INT;
    G1TargetPose : SM3_Robotics.SMC_POS_REF;
    G1CoordSystem : SMC_COORD_SYSTEM;
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
    // Phase 4 step 2: belt_kind dispatches the bind handler between
    // MC_TrackConveyorBelt (linear, default) and MC_TrackRotaryTable
    // (future). Empty / absent treated as 'linear' for backwards compat.
    BindKind         : STRING(16);
    // Phase 4 step 3: M4 'action' field dispatches FlyEvent payload
    // between pin operations (default, legacy) and COORD1_BIND
    // (scheduled track bind at a specific belt pulse).
    M4ActionStr      : STRING(16);

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
    // Monotonic DINT accumulation (no 16-bit wrap) so visualisations
    // see a single straight ramp instead of sawtooth jumps. Real EC
    // encoders also won't wrap inside a test run (~2.1e9 pulse headroom
    // on a 32-bit signed counter), so this matches the eventual
    // hardware path; the bridge code below already handles arbitrarily
    // large values cleanly via TO_LREAL.
    IF GVL.ConveyorPulseSyntheticEnable THEN
        GVL.ConveyorPulseRaw := GVL.ConveyorPulseRaw + GVL.ConveyorPulseSyntheticStep;
    END_IF;

    // Phase 4 bridge: power the conveyor virtual axis + drive its
    // position from ConveyorPulseRaw. Write BOTH fSetPosition and
    // fActPosition: the virtual drive's internal controller has a
    // velocity limit that makes fActPosition lag fSetPosition under
    // fast changes (observed ~400mm lag at synthetic step=200). For
    // a pure encoder-bridge use case the controller dynamics are
    // unwanted -- writing fActPosition directly makes the axis behave
    // as a pure position sensor. MC_TrackConveyorBelt reads
    // fActPosition, so this is the field that matters.
    conveyorPower(Axis := ConveyorEncoderAxis, Enable := TRUE,
                  bRegulatorOn := TRUE, bDriveStart := TRUE);
    ConveyorEncoderAxis.fSetPosition := TO_LREAL(GVL.ConveyorPulseRaw) * Coord1PulseToMm;
    ConveyorEncoderAxis.fActPosition := TO_LREAL(GVL.ConveyorPulseRaw) * Coord1PulseToMm;

    // Phase 4 step 2: edge-trigger MC_TrackConveyorBelt off GVL.Coord1Bound.
    // COORD1_BIND handler atomically writes Coord1BeltOriginRef then sets
    // Coord1Bound := TRUE; the rising edge here links PCS_1 to the belt.
    // COORD1_UNBIND sets Coord1Bound := FALSE -> falling edge releases.
    // Rebind during an active bind is rejected at the handler level
    // (must UNBIND -> BIND) so we don't need a two-scan toggle dance.
    trackConveyorBeltFb(
        AxisGroup := AxisGroupManagerFb._agAxisGroup,
        ConveyorBelt := ConveyorEncoderAxis,
        ConveyorBeltOrigin := Coord1BeltOriginRef,
        CoordSystem := SMC_COORD_SYSTEM.PCS_1,
        Execute := GVL.Coord1Bound
    );

    // Per-scan Coord1 origin recompute -- DIAGNOSTIC ONLY (Phase 4 step 2).
    // The real PCS_1 origin used by G1 frame=1 motion comes from
    // MC_TrackConveyorBelt, not from this computation. We keep the linear
    // formula running so GET_COORD1_DEBUG can report the host-side
    // prediction of "where PCS_1 origin SHOULD be in WCS right now" --
    // mismatch vs. the actual robot pose is a useful sanity check for
    // wrong belt direction / wrong scale during commissioning.
    //   Linear mode: OriginNow[i] = RefXyz[i] + (Pulse-RefPulse)/Scale[i].
    //                Scale[i]=0 locks that axis (only the belt direction
    //                needs a non-zero scale).
    //   Table  mode: code path retained for symmetry but Phase 4 step 2
    //                rejects cam_table bind, so this branch never fires.
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


    // Phase 4 step 3: COORD1 tracking window-exit detector. Once Coord1
    // is bound and the conveyor pulse has crossed Coord1ExitPulse, the
    // pick window is over (either the arm caught up but spent too long
    // grabbing, or it never caught up at all). Latch the diagnostic
    // context for the COORD1_ERROR event push below and trigger EV_ERROR
    // so the supervisor freezes the arm via MC_GroupStop. Coord1ExitPulse
    // sentinel -1 means "no window check" (dev/test SYS BIND path).
    // Skip when already in Error/UnInited so we don't re-latch each scan
    // while the host is recovering.
    IF GVL.Coord1Bound AND GVL.Coord1ExitPulse >= 0
       AND GVL.ConveyorPulseRaw > GVL.Coord1ExitPulse
       AND AxisGroupManagerFb._eState <> E_RobotState.Error
       AND AxisGroupManagerFb._eState <> E_RobotState.UnInited THEN
        GVL.Coord1ErrPending     := TRUE;
        GVL.Coord1ErrAtMovementId := CurrentMovementId;
        GVL.Coord1ErrAtProgress  := CurrentMovementIdProgress;
        GVL.Coord1ErrAtPulse     := GVL.ConveyorPulseRaw;
        GVL.Coord1ErrAtArmX      := ReadPosition.c.x;
        GVL.Coord1ErrAtArmY      := ReadPosition.c.y;
        GVL.Coord1ErrAtArmZ      := ReadPosition.c.z;
        GVL.Coord1ErrAtPcsX      := Pcs1KernelTransform.X;
        GVL.Coord1ErrAtPcsY      := Pcs1KernelTransform.Y;
        GVL.Coord1ErrAtPcsZ      := Pcs1KernelTransform.Z;
        GVL.Coord1ErrRefPulse    := GVL.Coord1RefPulse;
        GVL.Coord1ErrExitPulse   := GVL.Coord1ExitPulse;
        GVL.LastErrorSource      := 'Coord1:window_exit';
        AxisGroupManagerFb.Transition(E_RobotEvent.EV_ERROR);
    END_IF;


    // Phase 4 step 3: MC_GroupStop while the FSM is in Error. Brings the
    // arm to a controlled deceleration stop and locks the AxisGroup so
    // no further motion goes through until EV_RESET cycles us out. Uses
    // the latest G1's decel/jerk so the stop matches the motion profile
    // the operator expected -- not a hard E-stop.
    IF AxisGroupManagerFb._eState = E_RobotState.Error THEN
        GroupStopExecute := TRUE;
    ELSE
        GroupStopExecute := FALSE;
    END_IF;
    GroupStopFb(
        AxisGroup := AxisGroupManagerFb._agAxisGroup,
        Execute := GroupStopExecute,
        Deceleration := LatestDeceleration,
        Jerk := LatestJerk
    );


    // Phase 4 step 3: emit a single COORD1_ERROR event when the
    // window-exit detector latches a fault. Auto-unbind so a host that
    // sees the event + state=Error can do its normal Reset->climb
    // sequence and start the next cycle clean. Drop-newest on ring
    // overflow per the SPSC rationale documented in UpdateMotionProgress.
    IF GVL.Coord1ErrPending THEN
        IF (GVL.reMP_info_ridx.Space() > 0) THEN
            EventPacketPointer := ADR(GVL.reMP_info_arr[GVL.reMP_info_ridx.GetHead()]);
            MsgPakInfoClear(EventPacketPointer^);
            EventPacketPointer^.ele_count := 15;
            EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('kind'));
            EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('event'));
            EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('name'));
            EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('COORD1_ERROR'));
            EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('event_id'));
            EventPacketPointer^.whead := MessagePackerFb.PackLINT(pStart := EventPacketPointer^.whead, liValue := TO_LINT(GVL.Coord1BindEventId));
            EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('ref_pulse'));
            EventPacketPointer^.whead := MessagePackerFb.PackDINT(pStart := EventPacketPointer^.whead, diValue := GVL.Coord1ErrRefPulse);
            EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('exit_pulse'));
            EventPacketPointer^.whead := MessagePackerFb.PackDINT(pStart := EventPacketPointer^.whead, diValue := GVL.Coord1ErrExitPulse);
            EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('pulse_at_err'));
            EventPacketPointer^.whead := MessagePackerFb.PackDINT(pStart := EventPacketPointer^.whead, diValue := GVL.Coord1ErrAtPulse);
            EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('movement_id'));
            EventPacketPointer^.whead := MessagePackerFb.PackLINT(pStart := EventPacketPointer^.whead, liValue := TO_LINT(GVL.Coord1ErrAtMovementId));
            EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('mv_progress'));
            EventPacketPointer^.whead := MessagePackerFb.PackREAL(pStart := EventPacketPointer^.whead, rValue := GVL.Coord1ErrAtProgress);
            EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('arm_x'));
            EventPacketPointer^.whead := MessagePackerFb.PackREAL(pStart := EventPacketPointer^.whead, rValue := TO_REAL(GVL.Coord1ErrAtArmX));
            EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('arm_y'));
            EventPacketPointer^.whead := MessagePackerFb.PackREAL(pStart := EventPacketPointer^.whead, rValue := TO_REAL(GVL.Coord1ErrAtArmY));
            EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('arm_z'));
            EventPacketPointer^.whead := MessagePackerFb.PackREAL(pStart := EventPacketPointer^.whead, rValue := TO_REAL(GVL.Coord1ErrAtArmZ));
            EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('pcs_x'));
            EventPacketPointer^.whead := MessagePackerFb.PackREAL(pStart := EventPacketPointer^.whead, rValue := TO_REAL(GVL.Coord1ErrAtPcsX));
            EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('pcs_y'));
            EventPacketPointer^.whead := MessagePackerFb.PackREAL(pStart := EventPacketPointer^.whead, rValue := TO_REAL(GVL.Coord1ErrAtPcsY));
            EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('pcs_z'));
            EventPacketPointer^.whead := MessagePackerFb.PackREAL(pStart := EventPacketPointer^.whead, rValue := TO_REAL(GVL.Coord1ErrAtPcsZ));
            EventPacketPointer^.whead := MessagePackerFb.PackString(pStart := EventPacketPointer^.whead, psValue := ADR('runtime_ms'));
            EventPacketPointer^.whead := MessagePackerFb.PackLINT(pStart := EventPacketPointer^.whead, liValue := RuntimeMs);
            MsgPakInfoWrapup(EventPacketPointer^);
            GVL.reMP_info_ridx.PushHead();
            GVL.Coord1ErrPending := FALSE;
            GVL.Coord1ErrCount   := GVL.Coord1ErrCount + 1;
            // Auto-release tracking so the next cycle starts clean.
            GVL.Coord1Bound      := FALSE;
            GVL.Coord1ExitPulse  := -1;
        ELSE
            GVL.ReMpDropCount := GVL.ReMpDropCount + 1;
            // No dedicated drop counter -- this should be rare; the ring
            // would have to be saturated by ST_CHG / move_done / heartbeat
            // events the operator hasn't read. The host still sees
            // state=Error from the supervisor; only the diagnostic
            // payload is lost.
        END_IF;
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
    DrainHostPackets();


	
	
	
	
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




    // Removed 2026-06-23: `IF FALSE THEN ... ProcessTaskPacket` block.
    // Type='TASK' was a planned third packet class (alongside 'SYS' and
    // 'M') that never got past stub. The DrainHostPackets() loop above
    // already NAKs any non-SYS / non-M packet as 'missing_type_field',
    // so a future 'TASK' implementation would slot into that method's
    // ELSIF chain rather than this dead trailing block.

    
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