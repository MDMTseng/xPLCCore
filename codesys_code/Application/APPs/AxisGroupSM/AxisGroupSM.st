
PROGRAM AxisGroupSM
    VAR CONSTANT
        // 32: one busy cycle can hold the tape shots (DistanceTrigger, up to
        // 3 s), side/bottom shots, lights and a feeder refill at once; 10
        // ran out (flyevent_buffer_full, chaos seed 53, 2026-09-25).
        FlyEventBufferSize: UDINT := 32;
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

        (* Per-axis simulation switch (bench mode; GVL.AxisSimMask docs).
           One SMC3_ReinitDrive per axis: re-runs the drive start-up with
           bVirtual per the target mask bit -- the runtime equivalent of
           the Device Editor "virtual mode" checkbox. Sequence only runs
           in UnInited (drives powerless, group disabled) and only after
           the host opted in via SYS SET_AXIS_SIM. AxisSimApplySeq:
           0 = idle, 1 = reinit in flight. Host handshake: the new mask
           is published (axes_sim_mask) only when the sequence completes,
           so a client that waits for the published mask before
           EV_POWER_ON can never power half-reinitialized drives. *)
        AxisSimReinit0    : SMC3_ReinitDrive;
        AxisSimReinit1    : SMC3_ReinitDrive;
        AxisSimReinit2    : SMC3_ReinitDrive;
        AxisSimReinitReel : SMC3_ReinitDrive;
        AxisSimApplySeq     : INT := 0;
        AxisSimDesired      : BYTE := 0;
        AxisSimApplyTarget  : BYTE := 0;
        AxisSimApplyStartMs : LINT := 0;
        AXIS_SIM_APPLY_TIMEOUT_MS : LINT := 10000;

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
        // Iteration scratch: pointer into FlyEventBuffer[Index] inside
        // ProcessFlyEventsAndIo's FOR loop. Replaces ~60 repeated
        // `FlyEventBuffer[Index].` derefs per scan -- compiler may already
        // CSE these but the source becomes much cleaner.
        pFly : POINTER TO FlyEventData;

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

        // Pending deferred-reply waits (review 2026-09-24 P0-3), one per
        // kind, indexed by WAIT_KIND_*. Registered in ProcessMotionPacket,
        // resolved in the main body (pending waits block).
        HostRxCountSeen : UDINT := 0;   // last GVL.HostRxCount seen (DrainHostPackets)
        WaitActive    : ARRAY[0..2] OF BOOL;
        WaitCmdId     : ARRAY[0..2] OF DINT;
        WaitStartMs   : ARRAY[0..2] OF LINT;
        WaitTimeoutMs : ARRAY[0..2] OF LINT;   // <= 0: none (motion/reel only)
        WaitPinMask   : UINT;
        WaitPinGroup  : UINT;
        WaitPinState  : UINT;
        WaitKind      : INT;
        WaitIdx       : INT;
        WaitDone      : BOOL;
        WaitAck       : BOOL;
        WaitErr       : STRING(31);
        WAIT_KIND_MOTION : INT := 0;
        WAIT_KIND_REEL   : INT := 1;
        WAIT_KIND_DIN    : INT := 2;
        WAIT_DIN_DEFAULT_TIMEOUT_MS : LINT := 30000;

        // TAPE_CYCLE composite (plc.md §Direction step 3): wait for the arm's
        // move to start, advance the reel, wait for it to stop, then arm the
        // distance-gated top-camera shots. One at a time; deferred reply.
        // TapeState: 1 wait motion, 2 wait reel, 3 arm the shots.
        TapeActive      : BOOL;
        TapeState       : INT;
        TapeCmdId       : DINT;
        TapeStartMs     : LINT;
        TapeT1          : LINT;      // motion trigger reached
        TapeT2          : LINT;      // reel stopped
        TapeTimeoutMs   : LINT;
        TapeTargetId    : SMC_Movement_Id;
        TapeTargetProg  : REAL;
        TapeReelDist    : REAL;
        TapeReelF       : REAL;
        TapeReelAcc     : REAL;
        TapeReelDec     : REAL;
        TapeReelJerk    : REAL;
        TapeFly         : FlyEventData;   // the shots, parsed at command time
        TapeDone        : BOOL;
        TapeOk          : BOOL;
        TapeErr         : STRING(31);
        TapeIdx         : INT;
        TapeOffset      : DINT;
        TAPE_DEFAULT_TIMEOUT_MS : LINT := 6000;

        // Push-notification (server-initiated event) packet pointer.
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
    // B3: result scratch for the fly-side Coord1CommitBind call in
    // ProcessFlyEventsAndIo (shares BindRejectReason with the SYS path).
    BindFlyCommitOk  : BOOL;
    // B2c: GroupActualPositionFb.Valid AND NOT .Error, latched once per
    // scan in UpdateAxisGroupState. Gates DistanceTrigger evaluation in
    // ProcessFlyEventsAndIo -- a stale/garbage TCP read (e.g. the
    // (0,0,0) a freshly downloaded app reports) must not fire triggers.
    ArmPositionValid : BOOL := FALSE;

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

    // B2a fix (analysis 1.6): consume trackConveyorBeltFb.Error. Pre-fix
    // NONE of this FB's outputs were read -- if the kernel refused or
    // dropped the tracking while Coord1Bound stayed TRUE, every frame=1
    // G1 kept passing the Coord1Bound gate and targeted a NON-tracking
    // PCS_1: silent wrong-position motion, invisible even to the
    // window-exit detector. Supervisor pattern: latch source/ID, close
    // the frame gate (Coord1Bound), trip EV_ERROR. Clearing Coord1Bound
    // also drops Execute, so the FB releases and this block self-disarms
    // (plus the state guard prevents re-latching while in Error).
    IF trackConveyorBeltFb.Error AND GVL.Coord1Bound
       AND AxisGroupManagerFb._eState <> E_RobotState.Error
       AND AxisGroupManagerFb._eState <> E_RobotState.UnInited THEN
        GVL.LastErrorSource := 'Coord1:TrackConveyorBelt';
        GVL.LastErrorID     := TO_DWORD(trackConveyorBeltFb.ErrorID);
        GVL.Coord1Bound     := FALSE;
        AxisGroupManagerFb.Transition(eEvent := E_RobotEvent.EV_ERROR);
    END_IF;

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

    // ---- Per-axis simulation mask (bench mode; GVL.AxisSimMask docs). ----
    // Applied mask is LIVE-DERIVED from the drives' own bVirtual flag --
    // the drives are the source of truth, which also captures the
    // device-tree "virtual mode" checkbox at boot (probed live
    // 2026-07-08: EAxis0.bVirtual=TRUE on this rig before any
    // SET_AXIS_SIM). The reinit-complete handshake falls out for free:
    // axes_sim_mask only changes when the drives actually flipped.
    GVL.AxisSimMaskApplied := 0;
    IF IoConfig_Globals.EAxis0.bVirtual THEN GVL.AxisSimMaskApplied := GVL.AxisSimMaskApplied OR 16#01; END_IF;
    IF IoConfig_Globals.EAxis1.bVirtual THEN GVL.AxisSimMaskApplied := GVL.AxisSimMaskApplied OR 16#02; END_IF;
    IF IoConfig_Globals.EAxis2.bVirtual THEN GVL.AxisSimMaskApplied := GVL.AxisSimMaskApplied OR 16#04; END_IF;
    IF reelpullmotor.bVirtual           THEN GVL.AxisSimMaskApplied := GVL.AxisSimMaskApplied OR 16#08; END_IF;
    // First scan, before any reinit can run: what the project configured.
    IF NOT GVL.AxisSimConfigCaptured THEN
        GVL.AxisSimConfigMask := GVL.AxisSimMaskApplied;
        GVL.AxisSimConfigCaptured := TRUE;
    END_IF;
    // Published effective mask: live drive state, with the legacy
    // all-axes gate OR'd in for backward compatibility (the daemon-
    // forced flag never touched drives pre-2026-07-08 and still
    // doesn't -- it only declares the all-virtual rig state, exactly
    // as the FORCE_SKIP gate always has).
    GVL.AxisSimMask := GVL.AxisSimMaskApplied OR SEL(GVL.bVirtualMotorsMode, 16#00, 16#0F);

    IF AxisGroupManagerFb._eState = E_RobotState.UnInited THEN
        // Request TTL: RuntimeMs deadline re-stamped on every accepted
        // SET_AXIS_SIM (see GVL.st for why not a TON). Only evaluated
        // here, so an applied mask is never yanked mid-session; the
        // next reset after expiry reverts to all-real.
        IF (GVL.AxisSimMask_Request <> 0)
           AND ((RuntimeMs - GVL.AxisSimMaskSetMs) > GVL.AXIS_SIM_TTL_MS) THEN
            GVL.AxisSimMask_Request := 0;
        END_IF;
        // Config mask OR'd in: a request can add simulation, never remove
        // it from an axis the project has virtual (GVL.AxisSimConfigMask).
        AxisSimDesired := GVL.AxisSimMask_Request OR GVL.AxisSimConfigMask
                          OR SEL(GVL.bVirtualMotorsMode, 16#00, 16#0F);
        IF (AxisSimApplySeq = 0) AND GVL.AxisSimHostRequested
           AND (AxisSimDesired <> GVL.AxisSimMaskApplied) THEN
            AxisSimApplyTarget  := AxisSimDesired;
            AxisSimApplyStartMs := RuntimeMs;
            AxisSimApplySeq     := 1;
        END_IF;
    ELSIF AxisSimApplySeq <> 0 THEN
        // FSM left UnInited mid-apply (host raced EV_POWER_ON instead of
        // waiting for the published mask). Abort: drop Execute so the
        // FBs reset; Applied stays at the old value so axes_sim_mask
        // keeps telling the truth about what was last fully applied.
        AxisSimApplySeq := 0;
    END_IF;

    // FB calls every scan (rising edge of bExecute starts the reinit;
    // falling edge after completion/abort resets the FBs).
    AxisSimReinit0(Axis := IoConfig_Globals.EAxis0,
                   bExecute := (AxisSimApplySeq = 1),
                   bVirtual := (AxisSimApplyTarget AND 16#01) <> 0);
    AxisSimReinit1(Axis := IoConfig_Globals.EAxis1,
                   bExecute := (AxisSimApplySeq = 1),
                   bVirtual := (AxisSimApplyTarget AND 16#02) <> 0);
    AxisSimReinit2(Axis := IoConfig_Globals.EAxis2,
                   bExecute := (AxisSimApplySeq = 1),
                   bVirtual := (AxisSimApplyTarget AND 16#04) <> 0);
    AxisSimReinitReel(Axis := reelpullmotor,
                      bExecute := (AxisSimApplySeq = 1),
                      bVirtual := (AxisSimApplyTarget AND 16#08) <> 0);

    IF AxisSimApplySeq = 1 THEN
        IF AxisSimReinit0.bError OR AxisSimReinit1.bError
           OR AxisSimReinit2.bError OR AxisSimReinitReel.bError THEN
            // Latch-and-abort: Applied keeps the last fully-applied mask,
            // so the host sees the mismatch in axes_sim_mask and can
            // retry. No EV_ERROR -- we are already in UnInited and the
            // drives are powerless; the latch is diagnostic.
            GVL.LastErrorSource := 'AxisSim:ReinitDrive';
            GVL.LastErrorID := TO_DWORD(AxisSimReinit0.nErrorID)
                OR TO_DWORD(AxisSimReinit1.nErrorID)
                OR TO_DWORD(AxisSimReinit2.nErrorID)
                OR TO_DWORD(AxisSimReinitReel.nErrorID);
            AxisSimApplySeq := 0;
        ELSIF AxisSimReinit0.bDone AND AxisSimReinit1.bDone
              AND AxisSimReinit2.bDone AND AxisSimReinitReel.bDone THEN
            // Applied is live-derived above; all-bDone just ends the
            // sequence (drops Execute so the FBs reset).
            AxisSimApplySeq := 0;
        ELSIF (RuntimeMs - AxisSimApplyStartMs) > AXIS_SIM_APPLY_TIMEOUT_MS THEN
            GVL.LastErrorSource := 'AxisSim:ReinitTimeout';
            GVL.LastErrorID := 0;
            AxisSimApplySeq := 0;
        END_IF;
    END_IF;

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
        ELSIF GroupReadStatusFb.GroupErrorStop THEN
            // SoftMotion kinematic-group ErrorStop. Previously this was
            // checked only in ProcessMotionPacket (silently NAK'd G1s with
            // err='group_error_stop'), so a trip mid-flight left the FSM
            // saying st=Ready while every motion packet was being rejected
            // -- the host saw a healthy state with all moves silently
            // failing. Surfacing here drives the FSM to Error so the host's
            // status path matches reality. ErrorID is usually SMC_NO_ERROR
            // on virtual-axis trips (the well-known "instant-complete"
            // fall-through); diagnostic source string distinguishes it
            // from .Error above.
            GVL.LastErrorSource := 'Supervisor:GroupErrorStop';
            GVL.LastErrorID := TO_DWORD(GroupReadStatusFb.ErrorID);
            GVL.GroupErrorStopTripCount := GVL.GroupErrorStopTripCount + 1;
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
        // B3 (analysis 2.1): snapshot latch unified with the fly-bind
        // fault path -- one Coord1LatchWindowError for every COORD1
        // fault source. eventId self-assign keeps the bind-time id.
        Coord1LatchWindowError(eventId := GVL.Coord1BindEventId,
                               refPulse := GVL.Coord1RefPulse);
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
        IF TryAcquireReplySlot(0) THEN
            MessagePackerFb.PackKvStr(ADR('kind'), ADR('event'));
            MessagePackerFb.PackKvStr(ADR('name'), ADR('COORD1_ERROR'));
            MessagePackerFb.PackKvLint(ADR('event_id'), TO_LINT(GVL.Coord1BindEventId));
            MessagePackerFb.PackKvDint(ADR('ref_pulse'), GVL.Coord1ErrRefPulse);
            MessagePackerFb.PackKvDint(ADR('exit_pulse'), GVL.Coord1ErrExitPulse);
            MessagePackerFb.PackKvDint(ADR('pulse_at_err'), GVL.Coord1ErrAtPulse);
            MessagePackerFb.PackKvLint(ADR('movement_id'), TO_LINT(GVL.Coord1ErrAtMovementId));
            MessagePackerFb.PackKvReal(ADR('mv_progress'), GVL.Coord1ErrAtProgress);
            MessagePackerFb.PackKvReal(ADR('arm_x'), TO_REAL(GVL.Coord1ErrAtArmX));
            MessagePackerFb.PackKvReal(ADR('arm_y'), TO_REAL(GVL.Coord1ErrAtArmY));
            MessagePackerFb.PackKvReal(ADR('arm_z'), TO_REAL(GVL.Coord1ErrAtArmZ));
            MessagePackerFb.PackKvReal(ADR('pcs_x'), TO_REAL(GVL.Coord1ErrAtPcsX));
            MessagePackerFb.PackKvReal(ADR('pcs_y'), TO_REAL(GVL.Coord1ErrAtPcsY));
            MessagePackerFb.PackKvReal(ADR('pcs_z'), TO_REAL(GVL.Coord1ErrAtPcsZ));
            MessagePackerFb.PackKvLint(ADR('runtime_ms'), RuntimeMs);
            CommitReplySlotEvent();
            GVL.Coord1ErrPending := FALSE;
            GVL.Coord1ErrCount   := GVL.Coord1ErrCount + 1;
            // Auto-release tracking so the next cycle starts clean.
            GVL.Coord1Bound      := FALSE;
            GVL.Coord1ExitPulse  := -1;
        END_IF;
        // No dedicated class counter -- TryAcquireReplySlot already bumps
        // GVL.ReMpDropCount on full-ring failure. The COORD1_ERROR push
        // would have to be queued behind a backlog of ST_CHG / move_done /
        // heartbeat to drop; host still sees state=Error via the
        // supervisor, only the diagnostic payload is lost.
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
	
	
   // Digital inputs. The first machine read them from a HECAT_1616 module
   // that is not in this machine's device tree, so the line was commented
   // out and every input read 0 (review 2026-09-24 P0-2). Here the coupler's
   // two input bytes are InputCHs[1] (%IB8, inputs 0-7) and InputCHs[2]
   // (%IB9, inputs 8-15), the same mapping as the outputs.
   // GVL.SimDigitalInputEnable substitutes GVL.SimDigitalInput for the
   // all-virtual scene, where the tape sensors the renderer's input
   // watchdog checks do not exist.
   IF GVL.SimDigitalInputEnable THEN
       DigitalInputPointer := ADR(GVL.SimDigitalInput);
   ELSE
       DigitalInputPointer := ADR(InputCHs[1]);
   END_IF

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

    // ---- Pending waits (review 2026-09-24 P0-3) ----
    // Resolve the deferred-reply waits registered by ProcessMotionPacket.
    // Runs before the not-Ready RETURN so a wait still pending when the
    // FSM leaves Ready is NAK'd group_not_ready instead of left hanging.
    // Host gone: dropped without a reply (nobody to answer; the next
    // client must not get it). Reply ring full: retried next scan.
    FOR WaitIdx := 0 TO 2 DO
        IF WaitActive[WaitIdx] THEN
            WaitDone := FALSE;
            WaitAck := FALSE;
            WaitErr := '';
            IF NOT TCP_MSGPAK_Server.fbMyServer.CLIENT.xActive THEN
                WaitActive[WaitIdx] := FALSE;
            ELSIF NOT AxisGroupReady THEN
                WaitDone := TRUE;
                WaitErr := 'group_not_ready';
            ELSE
                IF (WaitIdx = WAIT_KIND_MOTION) THEN
                    WaitAck := (GroupReadPositionFb.MovementId = 0);
                ELSIF (WaitIdx = WAIT_KIND_REEL) THEN
                    // reelGoRequest*: a ReelGo raises the Execute request
                    // one scan before MC_MoveRelative reports Busy.
                    WaitAck := NOT (reelGoRequest OR reelGoRequest2
                                    OR reelMoveRelative.Busy OR reelMoveRelative2.Busy);
                ELSE
                    WaitAck := (DigitalInputPointer <> 0)
                        AND ((DigitalInputPointer[WaitPinGroup] AND WaitPinMask) = (WaitPinState AND WaitPinMask));
                END_IF;
                IF WaitAck THEN
                    WaitDone := TRUE;
                ELSIF (WaitTimeoutMs[WaitIdx] > 0)
                      AND ((RuntimeMs - WaitStartMs[WaitIdx]) >= WaitTimeoutMs[WaitIdx]) THEN
                    WaitDone := TRUE;
                    WaitErr := 'block_timeout';
                END_IF;
            END_IF;
            IF WaitDone THEN
                IF TryAcquireReplySlot(ADR(GVL.ReMpDropCount_Reply)) THEN
                    IF NOT WaitAck THEN
                        MessagePackerFb.PackKvStr(ADR('err'), ADR(WaitErr));
                    END_IF;
                    CommitReplySlotAck(WaitCmdId[WaitIdx], WaitAck);
                    WaitActive[WaitIdx] := FALSE;
                END_IF;
            END_IF;
        END_IF;
    END_FOR;

    // ---- TAPE_CYCLE sequence (registered in ProcessMotionPacket) ----
    IF TapeActive THEN
        TapeDone := FALSE;
        TapeOk := FALSE;
        TapeErr := '';
        IF NOT TCP_MSGPAK_Server.fbMyServer.CLIENT.xActive THEN
            TapeActive := FALSE;                          // host gone: nobody to answer
        ELSIF NOT AxisGroupReady THEN
            TapeDone := TRUE;
            TapeErr := 'group_not_ready';
        ELSIF (RuntimeMs - TapeStartMs) > TapeTimeoutMs THEN
            TapeDone := TRUE;
            TapeErr := 'tape_timeout';
        ELSIF TapeState = 1 THEN
            // same test as a MovmentProgressTrigger fly event
            IF (CurrentMovementId > TapeTargetId)
               OR ((CurrentMovementId = TapeTargetId) AND (CurrentMovementIdProgress >= TapeTargetProg)) THEN
                TapeT1 := RuntimeMs;
                TapeT2 := RuntimeMs;
                IF TapeReelDist <> 0 THEN
                    // same dispatch as ReelGo
                    reelGoDistance := TapeReelDist;
                    reelGoVelocity := TapeReelF;
                    reelGoAccel    := TapeReelAcc;
                    reelGoDecel    := TapeReelDec;
                    reelGoJerk     := TapeReelJerk;
                    IF NOT reelMoveRelative.Busy THEN
                        reelGoRequest := TRUE;
                        TapeState := 2;
                    ELSIF NOT reelMoveRelative2.Busy THEN
                        reelGoRequest2 := TRUE;
                        TapeState := 2;
                    ELSE
                        TapeDone := TRUE;
                        TapeErr := 'reel_busy';
                    END_IF;
                ELSE
                    TapeState := 3;
                END_IF;
            END_IF;
        ELSIF TapeState = 2 THEN
            // reelGoRequest* covers the scan before MC_MoveRelative reports Busy
            IF NOT (reelGoRequest OR reelGoRequest2 OR reelMoveRelative.Busy OR reelMoveRelative2.Busy) THEN
                TapeT2 := RuntimeMs;
                TapeState := 3;
            END_IF;
        END_IF;
        IF TapeActive AND NOT TapeDone AND (TapeState = 3) THEN
            TapeDone := TRUE;
            TapeErr := 'flyevent_buffer_full';
            FOR TapeIdx := 0 TO TO_INT(FlyEventBufferSize - 1) DO
                IF FlyEventBuffer[TapeIdx].TriggerType = 0 THEN
                    FlyEventBuffer[TapeIdx] := TapeFly;
                    TapeOk := TRUE;
                    TapeErr := '';
                    EXIT;
                END_IF;
            END_FOR;
        END_IF;
        IF TapeDone THEN
            IF TryAcquireReplySlot(ADR(GVL.ReMpDropCount_Reply)) THEN
                IF TapeOk THEN
                    MessagePackerFb.PackKvLint(ADR('wait_ms'), TapeT1 - TapeStartMs);
                    MessagePackerFb.PackKvLint(ADR('reel_ms'), TapeT2 - TapeT1);
                ELSE
                    MessagePackerFb.PackKvStr(ADR('err'), ADR(TapeErr));
                    MessagePackerFb.PackKvDint(ADR('state'), TapeState);
                END_IF;
                CommitReplySlotAck(TapeCmdId, TapeOk);
                TapeActive := FALSE;
            END_IF;
        END_IF;
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