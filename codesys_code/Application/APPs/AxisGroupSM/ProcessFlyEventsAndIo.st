(* =========== IMPLEMENTATION =========== *)
// 2026-06-23 cleanup: trigger-arm + TTL boilerplate factored into
// ArmFiredTrigger() / TickTtl() helpers; pFly aliases the current slot
// once per iteration so the body reads `pFly^.X` instead of
// `FlyEventBuffer[Index].X` 20-odd times.
TriggerActive := FALSE;
FlyEventAvailableCount := 0;

FOR Index := 0 TO TO_INT(FlyEventBufferSize - 1) DO
    pFly := ADR(FlyEventBuffer[Index]);
    TriggerActive := FALSE;
    TriggerErrorCode := 0;

    CASE pFly^.TriggerType OF
        FlyEventTriggerType.DistanceTrigger:
            // Trigger by Cartesian distance from a fixed WCS point. Uses
            // squared distance to avoid SQRT on every scan. ReadPosition is
            // refreshed by UpdateAxisGroupState earlier in the same scan
            // (AxisGroupSM.st:346) so it's the actual TCP pose for this tick.
            //
            // B2c fix (analysis 1.6): gate the distance compare on
            // ArmPositionValid (GroupActualPositionFb.Valid AND NOT
            // .Error, latched in UpdateAxisGroupState). A stale/garbage
            // position read must not fire (or mis-hold) a trigger --
            // e.g. a freshly downloaded app reads TCP=(0,0,0) until the
            // first valid cycle (see commit 74a01c2). Deliberately NOT
            // an EV_ERROR trip: a transient invalid read must not kill
            // the machine -- we just skip the evaluation those scans.
            // TTL still decays so a persistently-invalid read ends in
            // the normal TRIGGER_TIMEOUT_ERR path instead of a stuck slot.
            IF NOT ArmPositionValid THEN
                IF TickTtl(pFly) THEN
                    TriggerActive := TRUE;
                    TriggerErrorCode := TRIGGER_TIMEOUT_ERR;
                END_IF;
            ELSE
                DistDx := ReadPosition.c.x - pFly^.TargetPos_X;
                DistDy := ReadPosition.c.y - pFly^.TargetPos_Y;
                DistDz := ReadPosition.c.z - pFly^.TargetPos_Z;
                DistSq := DistDx*DistDx + DistDy*DistDy + DistDz*DistDz;
                DistThresholdSq := pFly^.TargetDistance * pFly^.TargetDistance;
                // TriggerInBoundary=TRUE: fire on entering the ball (dist <= radius).
                // TriggerInBoundary=FALSE: fire on leaving the ball (dist >= radius).
                IF (pFly^.TriggerInBoundary AND DistSq <= DistThresholdSq)
                   OR (NOT pFly^.TriggerInBoundary AND DistSq >= DistThresholdSq) THEN
                    TriggerActive := ArmFiredTrigger(pFly);
                ELSIF TickTtl(pFly) THEN
                    TriggerActive := TRUE;
                    TriggerErrorCode := TRIGGER_TIMEOUT_ERR;
                END_IF;
            END_IF;

        FlyEventTriggerType.MovmentProgressTrigger:
            IF (
                CurrentMovementId > pFly^.TargetMovementId OR
                (CurrentMovementId = pFly^.TargetMovementId AND CurrentMovementIdProgress >= pFly^.TargetMovementProgress)
            ) THEN
                TriggerActive := ArmFiredTrigger(pFly);
            ELSIF TickTtl(pFly) THEN
                TriggerActive := TRUE;
                TriggerErrorCode := TRIGGER_TIMEOUT_ERR;
            END_IF;

        FlyEventTriggerType.PulseTrigger:
            // Phase 5: fire once GVL.ConveyorPulseRaw crosses the absolute
            // pulse target. Forward-only (>= test) -- a reset of the
            // conveyor encoder while events are pending will re-arm any
            // event with target <= current pulse, so producers must
            // re-issue M4s after a pulse-counter reset. TTL decay matches
            // DistanceTrigger / MovmentProgressTrigger semantics.
            IF (GVL.ConveyorPulseRaw >= pFly^.TargetPulseValue) THEN
                TriggerActive := ArmFiredTrigger(pFly);
            ELSIF TickTtl(pFly) THEN
                TriggerActive := TRUE;
                TriggerErrorCode := TRIGGER_TIMEOUT_ERR;
            END_IF;

        FlyEventTriggerType.SequenceDelayWait:
            IF (pFly^.IoStageIndex < pFly^.IoStageCount) THEN
                pFly^.IoStageDelayCountdown := pFly^.IoStageDelayCountdown - BusPeriod;
                IF (pFly^.IoStageDelayCountdown <= 0) THEN
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
        //
        // 2026-07-08: kind/name envelope added. Pre-fix this packet had
        // neither `kind` nor `id`, so the renderer's RX loop dropped it
        // on the floor -- the host could never learn that a registered
        // fly event (e.g. a heat-press pin op) timed out instead of
        // firing. Envelope now matches ST_CHG so PluginHello broadcasts
        // it as a `plc:event` (name='TRIGGER_ERR'). Extra keys are
        // backward compatible for the raw-socket test consumers, which
        // match on event_id/error_code.
        IF TryAcquireReplySlot(ADR(GVL.ReMpDropCount_TriggerEvent)) THEN
            MessagePackerFb.PackKvStr(ADR('kind'), ADR('event'));
            MessagePackerFb.PackKvStr(ADR('name'), ADR('TRIGGER_ERR'));
            MessagePackerFb.PackKvDint(ADR('error_code'), TriggerErrorCode);
            MessagePackerFb.PackKvDint(ADR('src_id'),     pFly^.SourceCommandId);
            MessagePackerFb.PackKvLint(ADR('event_id'),   TO_LINT(pFly^.EventId));
            CommitReplySlotEvent();
        END_IF;


        pFly^.ActionType := FlyEventActType.NOP;
        pFly^.TriggerType := FlyEventTriggerType.NOP;
    ELSE
        CASE pFly^.ActionType OF
            FlyEventActType.PIN_OPERATION:
                // Defensive bounds: guard against IoStageIndex >= IoStageCount
                // (post-increment lookup races) and against IoStageCount that
                // somehow exceeded FlyEventStageLimit (struct corruption).
                // Out-of-range deref of the fixed-size stage arrays would be
                // a hard crash; clamp to no-op instead.
                IF (pFly^.IoStageCount > 0
                    AND pFly^.IoStageIndex >= 0
                    AND pFly^.IoStageIndex < pFly^.IoStageCount
                    AND pFly^.IoStageIndex < FlyEventStageLimit) THEN
                    MotionOutputPin := pFly^.IoStagePinMask[pFly^.IoStageIndex];
                    StagePinState := pFly^.IoStagePinState[pFly^.IoStageIndex];
                ELSE
                    MotionOutputPin := 0;
                    StagePinState := 0;
                END_IF;

                TmpDigitalOutputBits := DigitalOutputBits;
                TmpDigitalOutputBits :=
                    (TmpDigitalOutputBits AND NOT (MotionOutputPin)) OR
                    ((StagePinState) AND (MotionOutputPin));
                DigitalOutputBits := TmpDigitalOutputBits;

                pFly^.IoStageIndex := pFly^.IoStageIndex + 1;
                IF (pFly^.IoStageIndex < pFly^.IoStageCount
                    AND pFly^.IoStageIndex < FlyEventStageLimit) THEN
                    pFly^.IoStageDelayCountdown := pFly^.IoStageDelay[pFly^.IoStageIndex];
                    pFly^.TriggerType := FlyEventTriggerType.SequenceDelayWait;
                ELSE
                    pFly^.ActionType := FlyEventActType.NOP;
                    pFly^.TriggerType := FlyEventTriggerType.NOP;
                END_IF;

                IoTriggerCount := IoTriggerCount + 1;

            FlyEventActType.COORD1_BIND:
                // Phase 4 step 3: apply the bind atomically when the
                // FlyEvent fires (typically via PulseTrigger at a precise
                // belt pulse). Belt is +X axis only for this phase, so
                // BindScaleY/Z are required to be 0 and BindScaleX != 0.
                //
                // B3 fix (analysis 2.1): validation + commit now live in
                // Coord1CommitBind -- the SAME method the SYS COORD1_BIND
                // handler calls -- so the two paths can never drift again.
                // The fly path thereby GAINS the MotionBufferSize>0
                // refusal it was missing (a bind firing under queued
                // motion used to silently re-anchor PCS_1 under the
                // in-flight move -- exactly the mid-flight frame change
                // the SYS path refuses). Trigger fire pulse is used as
                // ref_pulse so the bind anchors at exactly the encoder
                // value the host scheduled it for (PulseTrigger fires the
                // same scan ConveyorPulseRaw first crosses the target).
                //
                // Non-axial belt (ScaleY/Z != 0) is caller-side because
                // the method only sees the X component -- mirrors the SYS
                // handler's own caller-side 'belt_direction_not_axis_x'.
                IF pFly^.BindScaleY <> 0.0 OR pFly^.BindScaleZ <> 0.0 THEN
                    BindFlyCommitOk  := FALSE;
                    BindRejectReason := 'belt_direction_not_axis_x';
                ELSE
                    BindFlyCommitOk := Coord1CommitBind(
                        refX            := pFly^.BindRefXyzX,
                        refY            := pFly^.BindRefXyzY,
                        refZ            := pFly^.BindRefXyzZ,
                        scaleX          := pFly^.BindScaleX,
                        refPulse        := GVL.ConveyorPulseRaw,
                        exitPulseOffset := pFly^.BindExitPulseOffset,
                        eventId         := pFly^.EventId,
                        RejectReason    => BindRejectReason);
                END_IF;

                IF BindFlyCommitOk THEN
                    GVL.Coord1FlyBindActiveCount := GVL.Coord1FlyBindActiveCount + 1;
                ELSE
                    // Fault path: any refusal (rebind while bound, motion
                    // queued, malformed scale) is a host-side sequencing
                    // bug -> latch the error context for the COORD1_ERROR
                    // event push and trip the FSM. B4 fix (analysis 1.4):
                    // call Transition(EV_ERROR) DIRECTLY -- the old
                    // `InputEvent := EV_ERROR` level-latch was never reset
                    // back to EV_NONE, so a second fault after an operator
                    // GA_EV reset produced no edge and the FSM stayed
                    // Ready while the arm kept moving. (Same pattern as
                    // GA_EV and the supervisor; see the 2026-06-23
                    // edge-trigger fix comment in DrainHostPackets.)
                    Coord1LatchWindowError(eventId := pFly^.EventId,
                                           refPulse := 0);
                    GVL.LastErrorSource := CONCAT('Coord1Fly:', BindRejectReason);
                    GVL.LastErrorID     := 0;
                    AxisGroupManagerFb.Transition(eEvent := E_RobotEvent.EV_ERROR);
                END_IF;
                GVL.Coord1FlyBindCount := GVL.Coord1FlyBindCount + 1;
                pFly^.ActionType := FlyEventActType.NOP;
                pFly^.TriggerType := FlyEventTriggerType.NOP;

            FlyEventActType.ACK_SRC_ID://send ack with source command id
                // Drop-newest on overflow (SPSC race rationale in
                // UpdateMotionProgress). FlyEvent state is cleared either way
                // so we don't loop on the same one-shot.
                IF TryAcquireReplySlot(ADR(GVL.ReMpDropCount_Reply)) THEN
                    MessagePackerFb.PackKvLint(ADR('event_id'), TO_LINT(pFly^.EventId));
                    CommitReplySlotAck(pFly^.SourceCommandId, TRUE);
                END_IF;

                pFly^.ActionType := FlyEventActType.NOP;
                pFly^.TriggerType := FlyEventTriggerType.NOP;

        END_CASE;
    END_IF;

    IoTriggerInfoCount := IoTriggerInfoCount + 1;
END_FOR;

// 2026-07-08: the device I/O mapping (screenshot-confirmed) binds the two
// output bytes to OutputCHs[1] and OutputCHs[2], NOT [0]/[1]:
//   OutputCHs[1] <- %QB8  (device "CH1-8bit", physical outputs 0-7)
//   OutputCHs[2] <- %QB9  (device "CH2-8bit", physical outputs 8-15)
// The old code wrote [0]/[1], so the low byte (bits 0-7 -- exactly where
// pin_op masks live) landed in the UNMAPPED OutputCHs[0] and never
// reached hardware, while OutputCHs[2] (mapped to %QB9) was never driven.
// Write [1]/[2] so DigitalOutputBits bit N -> physical output N.
// (InputCHs[1]/[2] <- %IB8/%IB9 by the same mapping, but the DI consumers
// are still unwired -- DigitalInputPointer is null -- so inputs are
// mapped-but-unconsumed for now.)
OutputCHs[1] := TO_BYTE(DigitalOutputBits AND 16#FF);
OutputCHs[2] := TO_BYTE(SHR(DigitalOutputBits,8) AND 16#FF);


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

    // B2b fix (analysis 1.6): SetCoord0/1 ACK and set
    // GVL.CoordSystemConfigured BEFORE this FB runs -- pre-fix a later
    // .Error only landed in the DBG fields, so the G1 gate stayed open
    // against a transform the kernel never applied (G1s then executed
    // in whatever frame was active before). Close the gate and surface
    // the fault via the supervisor pattern. First-seen edge
    // (DbgSetCoordSeenError) guarantees a single Transition per attempt.
    GVL.CoordSystemConfigured := FALSE;
    GVL.LastErrorSource := 'Coord1:SetCoordTransform';
    GVL.LastErrorID     := TO_DWORD(SetCoordTransformFb.ErrorID);
    AxisGroupManagerFb.Transition(eEvent := E_RobotEvent.EV_ERROR);
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
