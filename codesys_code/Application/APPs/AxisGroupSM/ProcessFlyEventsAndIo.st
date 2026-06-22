(* =========== IMPLEMENTATION =========== *)
TriggerActive := FALSE;
FlyEventAvailableCount := 0;

FOR Index := 0 TO TO_INT(FlyEventBufferSize - 1) DO
    TriggerActive := FALSE;
    TriggerErrorCode := 0;

    CASE FlyEventBuffer[Index].TriggerType OF
        FlyEventTriggerType.DistanceTrigger:
            // Trigger by Cartesian distance from a fixed WCS point. Uses
            // squared distance to avoid SQRT on every scan. ReadPosition is
            // refreshed by UpdateAxisGroupState earlier in the same scan
            // (AxisGroupSM.st:346) so it's the actual TCP pose for this tick.
            DistDx := ReadPosition.c.x - FlyEventBuffer[Index].TargetPos_X;
            DistDy := ReadPosition.c.y - FlyEventBuffer[Index].TargetPos_Y;
            DistDz := ReadPosition.c.z - FlyEventBuffer[Index].TargetPos_Z;
            DistSq := DistDx*DistDx + DistDy*DistDy + DistDz*DistDz;
            DistThresholdSq := FlyEventBuffer[Index].TargetDistance * FlyEventBuffer[Index].TargetDistance;
            // TriggerInBoundary=TRUE: fire on entering the ball (dist <= radius).
            // TriggerInBoundary=FALSE: fire on leaving the ball (dist >= radius).
            IF (FlyEventBuffer[Index].TriggerInBoundary AND DistSq <= DistThresholdSq)
               OR (NOT FlyEventBuffer[Index].TriggerInBoundary AND DistSq >= DistThresholdSq) THEN
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

        FlyEventTriggerType.PulseTrigger:
            // Phase 5: fire once GVL.ConveyorPulseRaw crosses the absolute
            // pulse target. Forward-only (>= test) -- a reset of the
            // conveyor encoder while events are pending will re-arm any
            // event with target <= current pulse, so producers must
            // re-issue M4s after a pulse-counter reset. TTL decay matches
            // DistanceTrigger / MovmentProgressTrigger semantics.
            IF (GVL.ConveyorPulseRaw >= FlyEventBuffer[Index].TargetPulseValue) THEN
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
        // Drop-newest on overflow (see UpdateMotionProgress for SPSC race
        // rationale). The action-clear below still runs even if the error
        // event was dropped -- the FlyEvent itself is one-shot, not the
        // notification.
        IF (GVL.reMP_info_ridx.Space() > 0) THEN
            ResponsePacketPointer := ADR(GVL.reMP_info_arr[GVL.reMP_info_ridx.GetHead()]);
            MsgPakInfoClear(ResponsePacketPointer^);
            ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 3;

            ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('error_code'));
            ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TriggerErrorCode);

            ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('src_id'));
            ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := FlyEventBuffer[Index].SourceCommandId);


            ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('event_id'));
            ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := TO_LINT(FlyEventBuffer[Index].EventId));

            MsgPakInfoWrapup(ResponsePacketPointer^);
            GVL.reMP_info_ridx.PushHead();
        ELSE
            GVL.ReMpDropCount := GVL.ReMpDropCount + 1;
            GVL.ReMpDropCount_TriggerEvent := GVL.ReMpDropCount_TriggerEvent + 1;
        END_IF;


        FlyEventBuffer[Index].ActionType := FlyEventActType.NOP;
        FlyEventBuffer[Index].TriggerType := FlyEventTriggerType.NOP;
    ELSE
        CASE FlyEventBuffer[Index].ActionType OF
            FlyEventActType.PIN_OPERATION:
                // Defensive bounds: guard against IoStageIndex >= IoStageCount
                // (post-increment lookup races) and against IoStageCount that
                // somehow exceeded FlyEventStageLimit (struct corruption).
                // Out-of-range deref of the fixed-size stage arrays would be
                // a hard crash; clamp to no-op instead.
                IF (FlyEventBuffer[Index].IoStageCount > 0
                    AND FlyEventBuffer[Index].IoStageIndex >= 0
                    AND FlyEventBuffer[Index].IoStageIndex < FlyEventBuffer[Index].IoStageCount
                    AND FlyEventBuffer[Index].IoStageIndex < FlyEventStageLimit) THEN
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
                IF (FlyEventBuffer[Index].IoStageIndex < FlyEventBuffer[Index].IoStageCount
                    AND FlyEventBuffer[Index].IoStageIndex < FlyEventStageLimit) THEN
                    FlyEventBuffer[Index].IoStageDelayCountdown := FlyEventBuffer[Index].IoStageDelay[FlyEventBuffer[Index].IoStageIndex];
                    FlyEventBuffer[Index].TriggerType := FlyEventTriggerType.SequenceDelayWait;
                ELSE
                    FlyEventBuffer[Index].ActionType := FlyEventActType.NOP;
                    FlyEventBuffer[Index].TriggerType := FlyEventTriggerType.NOP;
                END_IF;

                IoTriggerCount := IoTriggerCount + 1;

            FlyEventActType.COORD1_BIND:
                // Phase 4 step 3: apply the bind atomically when the
                // FlyEvent fires (typically via PulseTrigger at a precise
                // belt pulse). Belt is +X axis only for this phase, so
                // BindScaleY/Z are required to be 0 and BindScaleX != 0.
                // If we're already bound, this is a host-side sequencing
                // bug -> raise EV_ERROR with reason 'rebind_active' so
                // the operator sees a loud failure instead of a silent
                // wrong target.
                IF GVL.Coord1Bound THEN
                    // Latch the error context using the FlyEvent's own
                    // bookkeeping data so the host can correlate.
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
                    GVL.Coord1ErrRefPulse    := 0;  // bind never happened, no ref
                    GVL.Coord1ErrExitPulse   := GVL.Coord1ExitPulse;
                    GVL.Coord1BindEventId    := FlyEventBuffer[Index].EventId;
                    InputEvent := E_RobotEvent.EV_ERROR;
                ELSIF FlyEventBuffer[Index].BindScaleX = 0.0
                      OR FlyEventBuffer[Index].BindScaleY <> 0.0
                      OR FlyEventBuffer[Index].BindScaleZ <> 0.0 THEN
                    // Reject malformed bind -- same SM error path but with
                    // different latched event context for the host.
                    GVL.Coord1ErrPending     := TRUE;
                    GVL.Coord1ErrAtPulse     := GVL.ConveyorPulseRaw;
                    GVL.Coord1BindEventId    := FlyEventBuffer[Index].EventId;
                    InputEvent := E_RobotEvent.EV_ERROR;
                ELSE
                    // Trigger fire pulse used as ref_pulse so the bind is
                    // anchored at exactly the encoder value the host
                    // scheduled it for. PulseTrigger fires the same scan
                    // ConveyorPulseRaw first crosses TargetPulseValue, so
                    // current GVL.ConveyorPulseRaw is the right anchor.
                    Coord1PulseToMm := 1.0 / FlyEventBuffer[Index].BindScaleX;
                    Coord1BeltOriginRef.X := FlyEventBuffer[Index].BindRefXyzX
                                             - TO_LREAL(GVL.ConveyorPulseRaw) * Coord1PulseToMm;
                    Coord1BeltOriginRef.Y := FlyEventBuffer[Index].BindRefXyzY;
                    Coord1BeltOriginRef.Z := FlyEventBuffer[Index].BindRefXyzZ;
                    Coord1BeltOriginRef.A := 0.0;
                    Coord1BeltOriginRef.B := 0.0;
                    Coord1BeltOriginRef.C := 0.0;
                    GVL.Coord1RefPulse   := GVL.ConveyorPulseRaw;
                    GVL.Coord1RefXyz[0]  := FlyEventBuffer[Index].BindRefXyzX;
                    GVL.Coord1RefXyz[1]  := FlyEventBuffer[Index].BindRefXyzY;
                    GVL.Coord1RefXyz[2]  := FlyEventBuffer[Index].BindRefXyzZ;
                    GVL.Coord1Scale[0]   := FlyEventBuffer[Index].BindScaleX;
                    GVL.Coord1Scale[1]   := 0.0;
                    GVL.Coord1Scale[2]   := 0.0;
                    GVL.Coord1ExitPulse  := GVL.ConveyorPulseRaw
                                           + FlyEventBuffer[Index].BindExitPulseOffset;
                    GVL.Coord1BindEventId := FlyEventBuffer[Index].EventId;
                    GVL.Coord1CamMode    := GVL.COORD1_MODE_LINEAR;
                    GVL.Coord1CamCount   := 0;
                    GVL.Coord1Bound      := TRUE;
                    GVL.Coord1FlyBindActiveCount := GVL.Coord1FlyBindActiveCount + 1;
                END_IF;
                GVL.Coord1FlyBindCount := GVL.Coord1FlyBindCount + 1;
                FlyEventBuffer[Index].ActionType := FlyEventActType.NOP;
                FlyEventBuffer[Index].TriggerType := FlyEventTriggerType.NOP;

            FlyEventActType.ACK_SRC_ID://send ack with source command id
                // Drop-newest on overflow (SPSC race rationale in
                // UpdateMotionProgress). FlyEvent state is cleared either way
                // so we don't loop on the same one-shot.
                IF (GVL.reMP_info_ridx.Space() > 0) THEN
                    ResponsePacketPointer := ADR(GVL.reMP_info_arr[GVL.reMP_info_ridx.GetHead()]);
                    MsgPakInfoClear(ResponsePacketPointer^);


                    ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count +3;//three elements: event_id, id, ack


                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('event_id'));
                    ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := TO_LINT(FlyEventBuffer[Index].EventId));



                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('id'));
                    ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := FlyEventBuffer[Index].SourceCommandId);

                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('ack'));
                    ResponsePacketPointer^.whead := MessagePackerFb.PackBool(pStart := ResponsePacketPointer^.whead, bValue := TRUE);

                    MsgPakInfoWrapup(ResponsePacketPointer^);
                    GVL.reMP_info_ridx.PushHead();
                ELSE
                    GVL.ReMpDropCount := GVL.ReMpDropCount + 1;
                    GVL.ReMpDropCount_Reply := GVL.ReMpDropCount_Reply + 1;
                END_IF;

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

// Phase 4 step 2 diagnostic: read kernel-side PCS_1 origin in WCS.
// MC_TrackConveyorBelt mutates this every scan; comparing it to the
// host-side prediction (GVL.Coord1OriginNow, formula-driven from
// RefXyz/RefPulse/Scale) confirms the FB is actually tracking the
// belt rather than holding a stale snapshot.
ReadPcs1TransformFb(
    AxisGroup := AxisGroupManagerFb._agAxisGroup,
    Enable := TRUE,
    CoordSystem := SMC_COORD_SYSTEM.PCS_1,
    Valid => ,
    Busy => ,
    Error => ,
    ErrorID => ,
    CoordTransform => Pcs1KernelTransform
);
