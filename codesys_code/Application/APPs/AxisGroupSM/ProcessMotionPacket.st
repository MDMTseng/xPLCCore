(* =========== IMPLEMENTATION =========== *)

    IF (RetryMotionCommandCooldown = 0 AND PacketType = 'M' AND GVL.minfo_buf_ridx.Size() > 0 AND MotionBufferSize < MOTION_BUFFER_THRESHOLD) THEN
        ShouldReturn := FALSE;
        CommandAck := FALSE;

        // reMP overflow guard: drop-newest. Producer must NOT ConsumeTail
        // (Comm task is sole consumer; two consumers race on _tail). When
        // the ring is full we route the pack into a scratch slot so the
        // long branchy block below can stay unchanged; PushHead at the
        // bottom is gated on SlotAvailable. See GVL.reMP_info_scratch.
        IF (GVL.reMP_info_ridx.Space() > 0) THEN
            SlotAvailable := TRUE;
            ResponsePacketPointer := ADR(GVL.reMP_info_arr[GVL.reMP_info_ridx.GetHead()]);
        ELSE
            SlotAvailable := FALSE;
            GVL.ReMpDropCount := GVL.ReMpDropCount + 1;
            GVL.ReMpDropCount_Reply := GVL.ReMpDropCount_Reply + 1;
            ResponsePacketPointer := ADR(GVL.reMP_info_scratch);
        END_IF;
        MsgPakInfoClear(ResponsePacketPointer^);
        MessagePackerFb.BindSlot(ResponsePacketPointer);  // builder pattern: pack helpers target this slot

        IF (GroupReadStatusFb.Error = TRUE OR GroupReadStatusFb.GroupErrorStop) THEN
            // Include an `err` string so the UI's NAK handler gets a readable
            // reason instead of the generic "nak (id=..., cmd=...)" fallback.
            IF (GroupReadStatusFb.GroupErrorStop) THEN
                MessagePackerFb.PackKvStr(ADR('err'), ADR('group_error_stop'));
            ELSE
                MessagePackerFb.PackKvStr(ADR('err'), ADR('group_read_status_error'));
            END_IF;
            MessagePackerFb.PackKvLint(ADR('ERROR_CODE'), GroupReadStatusFb.ErrorID);

            ShouldReturn := TRUE;
            CommandAck := FALSE;
            GVL.minfo_buf_ridx.ConsumeTail();

        ELSIF (CommandType = 'BLOCK_FOR_MOTION_STOP' OR CommandType = 'WAIT_FOR_MOTION_STOP'
               OR CommandType = 'WAIT_FOR_REEL_STOP' OR CommandType = 'BLOCK_FOR_REEL_STOP'
               OR CommandType = 'BLOCK_FOR_DIGITAL_INPUT') THEN
            // Deferred-reply waits (review 2026-09-24 P0-3). Registered and
            // taken off the queue at once; the main body (pending waits
            // block) acks when the condition holds, NAKs block_timeout on
            // expiry or group_not_ready when the FSM leaves Ready. Packets
            // behind a wait are no longer held: a wait is not a barrier.
            // One pending wait per kind; a second one NAKs wait_busy.
            IF (CommandType = 'BLOCK_FOR_DIGITAL_INPUT') THEN
                WaitKind := WAIT_KIND_DIN;
            ELSIF (CommandType = 'WAIT_FOR_REEL_STOP' OR CommandType = 'BLOCK_FOR_REEL_STOP') THEN
                WaitKind := WAIT_KIND_REEL;
            ELSE
                WaitKind := WAIT_KIND_MOTION;
            END_IF;

            IF WaitActive[WaitKind] THEN
                MessagePackerFb.PackKvStr(ADR('err'), ADR('wait_busy'));
                ShouldReturn := TRUE;
                CommandAck := FALSE;
            ELSIF (WaitKind = WAIT_KIND_DIN)
                  AND (MessageUnpackerFb.TryReadINT64('group', 0) < 0
                       OR MessageUnpackerFb.TryReadINT64('group', 0) > 1) THEN
                // DigitalInputPointer[group] reads 2 bytes (SimDigitalInput
                // / InputCHs); a larger host index read past them.
                MessagePackerFb.PackKvStr(ADR('err'), ADR('bad_group'));
                ShouldReturn := TRUE;
                CommandAck := FALSE;
            ELSE
                WaitActive[WaitKind]    := TRUE;
                WaitCmdId[WaitKind]     := CommandId;
                WaitStartMs[WaitKind]   := RuntimeMs;
                WaitTimeoutMs[WaitKind] := MessageUnpackerFb.TryReadINT64('timeout_ms', 0);
                IF (WaitKind = WAIT_KIND_DIN) THEN
                    WaitPinMask  := TO_UINT(MessageUnpackerFb.TryReadINT64('pin', 0));
                    WaitPinGroup := TO_UINT(MessageUnpackerFb.TryReadINT64('group', 0));
                    WaitPinState := TO_UINT(MessageUnpackerFb.TryReadINT64('state', 0));
                    // "Wait forever" on an input is how the PLC wedged
                    // before; inputs get a ceiling instead.
                    IF (WaitTimeoutMs[WaitKind] <= 0) THEN
                        WaitTimeoutMs[WaitKind] := WAIT_DIN_DEFAULT_TIMEOUT_MS;
                    END_IF;
                END_IF;
                // No reply now: it is sent when the wait resolves.
            END_IF;
            GVL.minfo_buf_ridx.ConsumeTail();

        ELSIF (CommandType = 'GET_DIGITAL_INPUT') THEN
            //InputPinGroup := TO_UINT(MessageUnpackerFb.TryReadINT64('group', 0));



            IF DigitalInputPointer <> 0 THEN
                MessagePackerFb.PackKvDint(ADR('state'), DigitalInputPointer[0] OR SHL(TO_UDINT(DigitalInputPointer[1]), 8));
            ELSE
                // DigitalInputPointer not wired (HECAT_1616 line in TrackDigitalInputFlips is commented out).
                // Return 0 rather than dereferencing null.
                MessagePackerFb.PackKvDint(ADR('state'), 0);
            END_IF;

            ShouldReturn := TRUE;
            CommandAck := TRUE;
            GVL.minfo_buf_ridx.ConsumeTail();
        ELSIF (CommandType = 'READ_LATEST_CMD_LOCATION') THEN	

            MessagePackerFb.PackKvReal(ADR('X'), TO_REAL(MotionPose.c.X));
            MessagePackerFb.PackKvReal(ADR('Y'), TO_REAL(MotionPose.c.Y));
            MessagePackerFb.PackKvReal(ADR('Z'), TO_REAL(MotionPose.c.Z));
            MessagePackerFb.PackKvReal(ADR('A'), TO_REAL(MotionPose.c.A));
            MessagePackerFb.PackKvReal(ADR('B'), TO_REAL(MotionPose.c.B));
            MessagePackerFb.PackKvReal(ADR('C'), TO_REAL(MotionPose.c.C));

            ShouldReturn := TRUE;
            CommandAck := TRUE;
            GVL.minfo_buf_ridx.ConsumeTail();

        ELSIF (CommandType = 'G4') THEN
            // B1 fix (analysis 1.5, test_plc_refactor_findings_b2): do NOT
            // set ShouldReturn/CommandAck at branch entry. Pre-fix they were
            // set here, BEFORE SMC_GroupWait ran -- on CommandAccepted=FALSE
            // the packet stayed queued but the tail-commit still emitted
            // ack:TRUE (without movement_id), then every 100ms retry
            // cooldown re-acked the same id. Mirror the G1 branch: reply
            // only from the accepted / NAK paths.
            G4HaltTime := MessageUnpackerFb.TryReadREAL('P', - 1);

            IF (G4HaltTime > 0) THEN
                GroupWaitFb(
                    AxisGroup := AxisGroupManagerFb._agAxisGroup,
                    Execute := 1,
                    WaitTime := G4HaltTime
                );

                IF (GroupWaitFb.CommandAccepted) THEN
                    GVL.minfo_buf_ridx.ConsumeTail();

                    MessagePackerFb.PackKvLint(ADR('movement_id'), TO_LINT(GroupWaitFb.MovementId));

                    ShouldReturn := TRUE;
                    CommandAck := TRUE;
                ELSE
                    RetryMotionCommandCooldown := RETRY_COOLDOWN_G4_MS;
                END_IF;
            ELSE
                // P <= 0 (or missing): NAK with a reason (R2 spirit --
                // no bare NAKs).
                MessagePackerFb.PackKvStr(ADR('err'), ADR('bad_dwell'));
                GVL.minfo_buf_ridx.ConsumeTail();

                ShouldReturn := TRUE;
                CommandAck := FALSE;
            END_IF;

        ELSIF (CommandType = 'SetCoord0') THEN
            // 2026-06-23 code-review fix: refuse if a G1 is in flight.
            // Pulsing MC_SetCoordinateTransform while a move is executing
            // shifts the WCS origin under the move and the arm darts.
            // Mirrors COORD1_BIND's own motion-in-flight guard.
            IF MotionBufferSize > 0 THEN
                MessagePackerFb.PackKvStr(ADR('err'), ADR('motion_in_flight'));
                GVL.SetCoordBusyNakCount := GVL.SetCoordBusyNakCount + 1;
                ShouldReturn := TRUE;
                CommandAck := FALSE;
                GVL.minfo_buf_ridx.ConsumeTail();
            ELSE
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
                GVL.minfo_buf_ridx.ConsumeTail();
            END_IF

        ELSIF (CommandType = 'SetCoord1') THEN
            // Same guard as SetCoord0 -- see comment above.
            IF MotionBufferSize > 0 THEN
                MessagePackerFb.PackKvStr(ADR('err'), ADR('motion_in_flight'));
                GVL.SetCoordBusyNakCount := GVL.SetCoordBusyNakCount + 1;
                ShouldReturn := TRUE;
                CommandAck := FALSE;
                GVL.minfo_buf_ridx.ConsumeTail();
            ELSE
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
                GVL.minfo_buf_ridx.ConsumeTail();
            END_IF

        ELSIF (CommandType = 'RESET_DBG_INFO') THEN
            // Legacy M-path branch (wire-visible; some old callers use it).
            // R8 fix: delegate to the ONE canonical list so the two paths
            // can never drift again.
            ResetDiagCounters();
            ShouldReturn := TRUE;
            CommandAck := TRUE;
            GVL.minfo_buf_ridx.ConsumeTail();

        ELSIF (CommandType = 'TAPE_CYCLE') THEN
            // Tape step as one command (plc.md §Direction step 3): when the
            // move motion_id_offset / motion_progress relative to the last
            // accepted one starts, advance the reel by Distance (0 = none)
            // and wait for it, then arm the pin_op_seq shots as a
            // DistanceTrigger fly event (fire once the arm is outside the
            // sphere tx/ty/tz/td). Deferred reply {wait_ms, reel_ms} when
            // the shots are armed; NAK tape_busy / flyevent_buffer_full /
            // tape_timeout / reel_busy / group_not_ready. See the TAPE_CYCLE
            // block in AxisGroupSM.
            IF TapeActive THEN
                MessagePackerFb.PackKvStr(ADR('err'), ADR('tape_busy'));
                ShouldReturn := TRUE;
                CommandAck := FALSE;
            ELSIF (FlyEventAvailableCount <= 3) THEN
                MessagePackerFb.PackKvStr(ADR('err'), ADR('flyevent_buffer_full'));
                ShouldReturn := TRUE;
                CommandAck := FALSE;
            ELSE
                TapeTargetId := GroupReadStatusFb.LastAcceptedMovementId;
                TapeOffset := TO_DINT(MessageUnpackerFb.TryReadINT64('motion_id_offset', 0));
                IF TapeOffset >= 0 THEN
                    TapeTargetId := TapeTargetId + DINT_TO_ULINT(TapeOffset);
                ELSIF TapeTargetId >= DINT_TO_ULINT(-TapeOffset) THEN
                    TapeTargetId := TapeTargetId - DINT_TO_ULINT(-TapeOffset);
                ELSE
                    TapeTargetId := 0;
                END_IF;
                TapeTargetProg := MessageUnpackerFb.TryReadREAL('motion_progress', 0);
                TapeReelDist := MessageUnpackerFb.TryReadREAL('Distance', 0);
                TapeReelF    := MessageUnpackerFb.TryReadREAL('F',    reelGoVelocity);
                TapeReelAcc  := MessageUnpackerFb.TryReadREAL('ACC',  reelGoAccel);
                TapeReelDec  := MessageUnpackerFb.TryReadREAL('DEA',  reelGoDecel);
                TapeReelJerk := MessageUnpackerFb.TryReadREAL('JERK', reelGoJerk);
                TapeTimeoutMs := MessageUnpackerFb.TryReadINT64('timeout_ms', 0);
                // Plan tracking: cells this step advances, added to
                // GVL.PlanCellsDone once the reel has actually moved.
                TapePlanCells := TO_DINT(MessageUnpackerFb.TryReadINT64('cells', 0));
                TapePlanKind  := TO_INT(MessageUnpackerFb.TryReadINT64('kind', 0));
                IF TapeTimeoutMs <= 0 THEN
                    TapeTimeoutMs := TAPE_DEFAULT_TIMEOUT_MS;
                END_IF;

                // the shots: a DistanceTrigger pin op, as M4 trig 120 builds it
                TapeFly.ActionType := FlyEventActType.PIN_OPERATION;
                TapeFly.TriggerType := FlyEventTriggerType.DistanceTrigger;
                TapeFly.SourceCommandId := CommandId;
                TapeFly.PinGrp := 0;
                TapeFly.TimeToLiveMs := TO_DINT(MessageUnpackerFb.TryReadINT64('ttl_ms', 3000));
                TapeFly.TargetMovementId := 0;
                TapeFly.TargetMovementProgress := 0;
                TapeFly.TargetPos_X := TO_LREAL(MessageUnpackerFb.TryReadREAL('tx', 0));
                TapeFly.TargetPos_Y := TO_LREAL(MessageUnpackerFb.TryReadREAL('ty', 0));
                TapeFly.TargetPos_Z := TO_LREAL(MessageUnpackerFb.TryReadREAL('tz', 0));
                TapeFly.TargetDistance := TO_LREAL(MessageUnpackerFb.TryReadREAL('td', 0));
                TapeFly.TriggerInBoundary := MessageUnpackerFb.TryReadINT64('tin', 0) <> 0;
                TapeFly.TargetPulseValue := 0;
                TapeFly.EventId := TO_DINT(MessageUnpackerFb.TryReadINT64('event_id', 0));
                TapeFly.IoStageCount := 0;
                TapeFly.IoStageIndex := 0;
                TapeFly.IoStageDelayCountdown := 0;
                FOR StageLoopIdx := 0 TO FlyEventStageLimit-1 DO
                    TapeFly.IoStageDelay[StageLoopIdx] := 0;
                    TapeFly.IoStagePinMask[StageLoopIdx] := 0;
                    TapeFly.IoStagePinState[StageLoopIdx] := 0;
                END_FOR;
                IF MessageUnpackerFb.TryReadObject('pin_op_seq', ADR(fbPinOpSeqUnpacker)) THEN
                    fbPinOpSeqUnpacker.UnpackNext();
                    FOR PinOpSeqIdx := 0 TO (fbPinOpSeqUnpacker.uiLastCount / 3) - 1 DO
                        IF TapeFly.IoStageCount >= FlyEventStageLimit THEN
                            EXIT;
                        END_IF;
                        fbPinOpSeqUnpacker.UnpackNext();
                        StageDelayTemp := TO_INT(fbPinOpSeqUnpacker.diLastDINT);
                        fbPinOpSeqUnpacker.UnpackNext();
                        IF fbPinOpSeqUnpacker.diLastDINT < 0 THEN StagePinMaskTemp := 0;
                        ELSE StagePinMaskTemp := DINT_TO_ULINT(fbPinOpSeqUnpacker.diLastDINT); END_IF;
                        fbPinOpSeqUnpacker.UnpackNext();
                        IF fbPinOpSeqUnpacker.diLastDINT < 0 THEN StagePinStateTemp := 0;
                        ELSE StagePinStateTemp := DINT_TO_ULINT(fbPinOpSeqUnpacker.diLastDINT); END_IF;
                        IF StagePinMaskTemp <> 0 THEN
                            TapeFly.IoStageDelay[TapeFly.IoStageCount] := StageDelayTemp;
                            TapeFly.IoStagePinMask[TapeFly.IoStageCount] := StagePinMaskTemp;
                            TapeFly.IoStagePinState[TapeFly.IoStageCount] := StagePinStateTemp;
                            TapeFly.IoStageCount := TapeFly.IoStageCount + 1;
                        END_IF;
                    END_FOR;
                END_IF;

                // Reserve the shots' slot now, before the reel moves, so a
                // full buffer is refused here instead of after the advance.
                TapeSlotIdx := -1;
                FOR TapeIdx := 0 TO TO_INT(FlyEventBufferSize - 1) DO
                    IF FlyEventBuffer[TapeIdx].TriggerType = FlyEventTriggerType.NOP THEN
                        FlyEventBuffer[TapeIdx].TriggerType := FlyEventTriggerType.Reserved;
                        TapeSlotIdx := TapeIdx;
                        EXIT;
                    END_IF;
                END_FOR;
                IF TapeSlotIdx < 0 THEN
                    MessagePackerFb.PackKvStr(ADR('err'), ADR('flyevent_buffer_full'));
                    ShouldReturn := TRUE;
                    CommandAck := FALSE;
                ELSE
                    TapeCmdId := CommandId;
                    TapeStartMs := RuntimeMs;
                    TapeState := 1;
                    TapeActive := TRUE;
                    // no reply now: it comes when the shots are armed
                END_IF;
            END_IF;
            GVL.minfo_buf_ridx.ConsumeTail();

        ELSIF (CommandType = 'ReelGo') THEN
            // NAK instead of acking a move that will not happen: while a
            // TAPE_CYCLE owns the reel (it moves through the same reelGo*
            // parameters, which this would overwrite), or when both FB
            // instances are busy (active + queued). Both used to ack TRUE.
            IF TapeActive THEN
                MessagePackerFb.PackKvStr(ADR('err'), ADR('tape_busy'));
                CommandAck := FALSE;
            ELSIF reelMoveRelative.Busy AND reelMoveRelative2.Busy THEN
                MessagePackerFb.PackKvStr(ADR('err'), ADR('reel_busy'));
                CommandAck := FALSE;
            ELSE
                reelGoDistance := MessageUnpackerFb.TryReadREAL('Distance', reelGoDistance);
                reelGoVelocity := MessageUnpackerFb.TryReadREAL('F',        reelGoVelocity);
                reelGoAccel    := MessageUnpackerFb.TryReadREAL('ACC',      reelGoAccel);
                reelGoDecel    := MessageUnpackerFb.TryReadREAL('DEA',      reelGoDecel);
                reelGoJerk     := MessageUnpackerFb.TryReadREAL('JERK',     reelGoJerk);
                IF NOT reelMoveRelative.Busy THEN
                    reelGoRequest  := TRUE;
                ELSE
                    reelGoRequest2 := TRUE;
                END_IF;
                CommandAck := TRUE;
            END_IF;
            ShouldReturn := TRUE;
            GVL.minfo_buf_ridx.ConsumeTail();

        ELSIF (CommandType = 'M4') THEN

				MotionTargetMovementId := TO_ULINT(MessageUnpackerFb.TryReadUINT64('motion_id', TO_ULINT(GroupReadStatusFb.LastAcceptedMovementId)));


				MessagePackerFb.PackKvLint(ADR('ev_buf_space'), TO_LINT(FlyEventAvailableCount));



				IF (FlyEventAvailableCount > 3) THEN

					// Phase 4 step 3: 'action' field switches between pin
					// operation (default, legacy) and scheduled COORD1_BIND.
					M4ActionStr := MessageUnpackerFb.TryReadString('action', 'pin_op');
					IF M4ActionStr = 'coord1_bind' THEN
						FlyEventTempData.ActionType := FlyEventActType.COORD1_BIND;
					ELSE
						FlyEventTempData.ActionType := FlyEventActType.PIN_OPERATION;
					END_IF;

					// Optional 'trig' field selects trigger family. Default
					// 20 = MovmentProgressTrigger preserves legacy callers
					// that omit the field. 120 = DistanceTrigger uses a
					// Cartesian point + radius gate instead of motion_id.
					// 130 = PulseTrigger gates on GVL.ConveyorPulseRaw
					// threshold (Phase 5: conveyor pick).
					FlyEventTriggerSelect := TO_INT(MessageUnpackerFb.TryReadINT64('trig', 20));
					IF FlyEventTriggerSelect = 120 THEN
						FlyEventTempData.TriggerType := FlyEventTriggerType.DistanceTrigger;
					ELSIF FlyEventTriggerSelect = 130 THEN
						FlyEventTempData.TriggerType := FlyEventTriggerType.PulseTrigger;
					ELSE
						FlyEventTempData.TriggerType := FlyEventTriggerType.MovmentProgressTrigger;
					END_IF;

					FlyEventTempData.SourceCommandId := CommandId;

					FlyEventTempData.PinGrp := 0;

					// 2026-06-23: legacy 'reset_ms' wire field removed. Hosts now
					// pass the equivalent 2-stage sequence via pin_op_seq (handled
					// below). lib/protocol.ts cmd.M4 keeps the `reset_ms:N` host
					// API alive by expanding to pin_op_seq client-side, so TS call
					// sites don't change; python tests use tests/_raw_plc.m4_pulse_seq.

					FlyEventTtlRaw := MessageUnpackerFb.TryReadINT64('ttl_ms', -1);
					IF (FlyEventTtlRaw < 0) OR (FlyEventTtlRaw > 2147483647) THEN
						FlyEventTempData.TimeToLiveMs := -1;
					ELSE
						FlyEventTempData.TimeToLiveMs := TO_DINT(FlyEventTtlRaw);
					END_IF;

					// Read offset as full DINT (was TO_INT, which silently wrapped
					// any value >32767 to negative and rolled TargetMovementId
					// backwards onto a stale movement). Negative offsets that
					// would underflow ULINT are clamped to 0.
					MotionIdOffsetTemp := TO_DINT(MessageUnpackerFb.TryReadINT64('motion_id_offset', 0));
					IF MotionIdOffsetTemp >= 0 THEN
						FlyEventTempData.TargetMovementId := MotionTargetMovementId + DINT_TO_ULINT(MotionIdOffsetTemp);
					ELSIF MotionTargetMovementId >= DINT_TO_ULINT(-MotionIdOffsetTemp) THEN
						FlyEventTempData.TargetMovementId := MotionTargetMovementId - DINT_TO_ULINT(-MotionIdOffsetTemp);
					ELSE
						FlyEventTempData.TargetMovementId := 0;
					END_IF;

					FlyEventTempData.TargetMovementProgress := TO_LREAL(MessageUnpackerFb.TryReadREAL('motion_progress', 1));

					// DistanceTrigger payload (ignored by MovmentProgressTrigger).
					// Defaults to (0,0,0)/r=0/inside; with r=0 and TriggerInBoundary=TRUE
					// the trigger fires only at the exact origin -- safe default that
					// just won't fire on its own. tin: 0=outside, 1=inside.
					FlyEventTempData.TargetPos_X := TO_LREAL(MessageUnpackerFb.TryReadREAL('tx', 0));
					FlyEventTempData.TargetPos_Y := TO_LREAL(MessageUnpackerFb.TryReadREAL('ty', 0));
					FlyEventTempData.TargetPos_Z := TO_LREAL(MessageUnpackerFb.TryReadREAL('tz', 0));
					FlyEventTempData.TargetDistance := TO_LREAL(MessageUnpackerFb.TryReadREAL('td', 0));
					FlyEventTempData.TriggerInBoundary := MessageUnpackerFb.TryReadINT64('tin', 1) <> 0;

					// PulseTrigger payload (ignored by other trigger types). Default
					// 0 means "fire immediately on next scan if ConveyorPulseRaw>=0",
					// which for an idle conveyor (Pulse=0) and a forgotten field is
					// safe-ish only with a positive TTL. Callers should supply an
					// explicit pulse target.
					FlyEventTempData.TargetPulseValue := TO_DINT(MessageUnpackerFb.TryReadINT64('pulse_target', 0));

					// COORD1_BIND action payload (Phase 4 step 3). Only meaningful
					// when ActionType = COORD1_BIND; other actions ignore. Pulled
					// off the wire unconditionally to keep the parser simple --
					// stale reads cost a handful of cycles, not correctness.
					FlyEventTempData.BindRefXyzX := TO_LREAL(MessageUnpackerFb.TryReadREAL('ref_xyz[0]', 0));
					FlyEventTempData.BindRefXyzY := TO_LREAL(MessageUnpackerFb.TryReadREAL('ref_xyz[1]', 0));
					FlyEventTempData.BindRefXyzZ := TO_LREAL(MessageUnpackerFb.TryReadREAL('ref_xyz[2]', 0));
					FlyEventTempData.BindScaleX  := TO_LREAL(MessageUnpackerFb.TryReadREAL('scale[0]', 0));
					FlyEventTempData.BindScaleY  := TO_LREAL(MessageUnpackerFb.TryReadREAL('scale[1]', 0));
					FlyEventTempData.BindScaleZ  := TO_LREAL(MessageUnpackerFb.TryReadREAL('scale[2]', 0));
					FlyEventTempData.BindExitPulseOffset := TO_DINT(MessageUnpackerFb.TryReadINT64('exit_pulse_offset', 0));

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



					// 2026-06-23: legacy `{pin, state, reset_ms}` single-pulse shape
					// removed. This block previously auto-appended one stage from
					// `pin` + `state` plus an optional reset stage from `ResetDelayMs`.
					// Both moved client-side (lib/protocol.ts expandM4ResetMs +
					// tests/_raw_plc.m4_pulse_seq) so the PLC has a single canonical
					// input: pin_op_seq.

					// Registration gate: progress trigger needs a real target movement id;
					// distance + pulse triggers don't (they gate on Cartesian pose /
					// conveyor pulse), so accept regardless of MotionTargetMovementId
					// for those branches. COORD1_BIND action has no IO stages and
					// MUST be paired with PulseTrigger (gates the bind on a precise
					// conveyor pulse); refuse the registration otherwise so a host
					// can't accidentally end up with a never-firing bind sitting in
					// the slot.
					IF (FlyEventTempData.ActionType = FlyEventActType.COORD1_BIND
					    AND FlyEventTempData.TriggerType = FlyEventTriggerType.PulseTrigger
					    AND FlyEventTempData.BindExitPulseOffset > 0)
					   OR (FlyEventTempData.ActionType = FlyEventActType.PIN_OPERATION
					       AND FlyEventTempData.IoStageCount > 0
					       AND (MotionTargetMovementId <> 0
					            OR FlyEventTempData.TriggerType = FlyEventTriggerType.DistanceTrigger
					            OR FlyEventTempData.TriggerType = FlyEventTriggerType.PulseTrigger)) THEN

						FOR Index := 0 TO TO_INT( FlyEventBufferSize - 1) DO

							IF (FlyEventBuffer[Index].TriggerType = 0) THEN

								FlyEventBuffer[Index] := FlyEventTempData;

								CommandAck := TRUE;

								EXIT;

							END_IF;

						END_FOR;

						IoCommandCount := IoCommandCount + 1;

						ShouldReturn := TRUE;

					ELSE
						// R3/R4 fix (test_plc_refactor_findings): a rejected
						// registration used to be consumed with no reply and
						// no counter -- a no_silent_drops violation the host
						// could only see as a timeout. NAK with the reason.
						ShouldReturn := TRUE;
						CommandAck := FALSE;
						MessagePackerFb.PackKvStr(ADR('err'), ADR('flyevent_reject'));
						GVL.FlyEventRejectNakCount := GVL.FlyEventRejectNakCount + 1;
					END_IF;

					GVL.minfo_buf_ridx.ConsumeTail();

				ELSE
					// R11 fix: with <=3 free slots the M4 used to sit
					// unconsumed at the minfo tail; the dispatcher EXITs on a
					// type-M tail, so EVERY packet behind it (SYS included)
					// stalled with zero counters -- an invisible full-queue
					// wedge. NAK + consume keeps the queue draining; the
					// packet is retryable by the host once slots free up.
					ShouldReturn := TRUE;
					CommandAck := FALSE;
					MessagePackerFb.PackKvStr(ADR('err'), ADR('flyevent_buffer_full'));
					GVL.FlyEventBufferFullNakCount := GVL.FlyEventBufferFullNakCount + 1;
					GVL.minfo_buf_ridx.ConsumeTail();

				END_IF;

			

ELSIF (CommandType = 'WAIT_FOR_TRIGGER_MOTION_PROGRESS') THEN
            IF (FlyEventAvailableCount > 3) THEN

                MotionTargetMovementId := TO_ULINT(MessageUnpackerFb.TryReadINT64('motion_id', TO_LINT(GroupReadStatusFb.LastAcceptedMovementId)));

                // R5 fix: the old gate also required MotionOutputPin <> 0 --
                // a copy-paste leftover from the legacy M4 shape.
                // MotionOutputPin is ProcessFlyEventsAndIo's scratch (the pin
                // mask of the LAST FIRED pin-op stage), semantically unrelated
                // to this registration; until any pin-op ever fired it is 0
                // and every registration was refused -- silently (see ELSE).
                IF (MotionTargetMovementId <> 0) THEN
                    FlyEventTempData.ActionType := FlyEventActType.ACK_SRC_ID;
                    FlyEventTempData.TriggerType := FlyEventTriggerType.MovmentProgressTrigger;
                    // Same TO_INT->INT16 wrap fix as the M4 branch above.
                    MotionIdOffsetTemp := TO_DINT(MessageUnpackerFb.TryReadINT64('motion_id_offset', 0));
                    IF MotionIdOffsetTemp >= 0 THEN
                        FlyEventTempData.TargetMovementId := MotionTargetMovementId + DINT_TO_ULINT(MotionIdOffsetTemp);
                    ELSIF MotionTargetMovementId >= DINT_TO_ULINT(-MotionIdOffsetTemp) THEN
                        FlyEventTempData.TargetMovementId := MotionTargetMovementId - DINT_TO_ULINT(-MotionIdOffsetTemp);
                    ELSE
                        FlyEventTempData.TargetMovementId := 0;
                    END_IF;
                    FlyEventTempData.TargetMovementProgress := TO_LREAL(MessageUnpackerFb.TryReadREAL('motion_progress', 1));

                    FlyEventTempData.SourceCommandId := CommandId;


                    FlyEventTempData.EventId := TO_DINT(MessageUnpackerFb.TryReadINT64('event_id', 0));
                    FlyEventTtlRaw := MessageUnpackerFb.TryReadINT64('ttl_ms', -1);
                    IF (FlyEventTtlRaw < 0) OR (FlyEventTtlRaw > 2147483647) THEN
                        FlyEventTempData.TimeToLiveMs := -1;
                    ELSE
                        FlyEventTempData.TimeToLiveMs := TO_DINT(FlyEventTtlRaw);
                    END_IF;


                    FOR Index := 0 TO TO_INT(FlyEventBufferSize - 1) DO
                        IF (FlyEventBuffer[Index].TriggerType = 0) THEN
                            FlyEventBuffer[Index] := FlyEventTempData;
                            EXIT;
                        END_IF;
                    END_FOR;
                    // Registration OK: no immediate reply -- the ack is
                    // deferred and arrives as the ACK_SRC_ID event when the
                    // trigger fires (ShouldReturn deliberately stays FALSE).
                ELSE
                    // R5 fix: refusal used to consume the packet with
                    // ShouldReturn never set -> host timed out with nothing
                    // in any counter. NAK like every other reject path.
                    ShouldReturn := TRUE;
                    CommandAck := FALSE;
                    MessagePackerFb.PackKvStr(ADR('err'), ADR('flyevent_reject'));
                    GVL.FlyEventRejectNakCount := GVL.FlyEventRejectNakCount + 1;
                END_IF;
                GVL.minfo_buf_ridx.ConsumeTail();
            ELSE
                // R11 fix: same full-queue wedge as the M4 branch above.
                ShouldReturn := TRUE;
                CommandAck := FALSE;
                MessagePackerFb.PackKvStr(ADR('err'), ADR('flyevent_buffer_full'));
                GVL.FlyEventBufferFullNakCount := GVL.FlyEventBufferFullNakCount + 1;
                GVL.minfo_buf_ridx.ConsumeTail();
            END_IF;


        ELSIF (CommandType = 'G1') THEN
            // PH#6: dedupe scan. UI-side retry on TCP jitter or a double-
            // click can re-send the same G1 with the same CommandId. If
            // we already accepted that CommandId recently, replay the
            // cached movement_id with dedup:TRUE and skip the actual
            // MoveLinearAbsoluteFb.Execute=1 -- otherwise we'd queue two
            // physical moves for one logical request. CommandId=0 is the
            // no-id sentinel and never matches.
            DupeMatchIdx := -1;
            IF (CommandId <> 0) THEN
                FOR DupeLoopIdx := 0 TO DUPE_RING_SIZE - 1 DO
                    IF (RecentCmdIds[DupeLoopIdx] = CommandId) THEN
                        DupeMatchIdx := DupeLoopIdx;
                        EXIT;
                    END_IF;
                END_FOR;
            END_IF;

            IF (DupeMatchIdx >= 0) THEN
                MessagePackerFb.PackKvLint(ADR('movement_id'), TO_LINT(RecentCmdMoveIds[DupeMatchIdx]));
                MessagePackerFb.PackKvBool(ADR('dedup'), TRUE);
                ShouldReturn := TRUE;
                CommandAck := TRUE;
                GVL.DupeCommandCount := GVL.DupeCommandCount + 1;
                GVL.minfo_buf_ridx.ConsumeTail();

            // Post-homing coord-system gate. Refuse G1 motion until the
            // operator has called SetCoord0 or SetCoord1 since the last
            // UnInited entry. Prevents machine-coord crashes when the UI
            // (or a test script) skips the SetCoord call after homing.
            ELSIF (NOT GVL.CoordSystemConfigured) THEN
                MessagePackerFb.PackKvStr(ADR('err'), ADR('coord_not_configured'));
                GVL.CoordNotConfiguredNakCount := GVL.CoordNotConfiguredNakCount + 1;
                ShouldReturn := TRUE;
                CommandAck := FALSE;
                GVL.minfo_buf_ridx.ConsumeTail();
            ELSE
            // Phase 3: read `frame` FIRST so we can NAK before mutating
            // persistent MotionPose / Latest* modal state. A NAK that
            // happens after the unpacks (the prior shape of this code)
            // poisoned the "previous XYZ / F / ACC" defaults that
            // subsequent omit-field G1s rely on.
            G1FrameTag := TO_INT(MessageUnpackerFb.TryReadINT64('frame', 0));
            IF (G1FrameTag <> 0 AND G1FrameTag <> 1) THEN
                MessagePackerFb.PackKvStr(ADR('err'), ADR('bad_frame'));
                GVL.Coord1BadFrameNakCount := GVL.Coord1BadFrameNakCount + 1;
                ShouldReturn := TRUE;
                CommandAck := FALSE;
                GVL.minfo_buf_ridx.ConsumeTail();
            ELSIF (G1FrameTag = 1 AND NOT GVL.Coord1Bound) THEN
                MessagePackerFb.PackKvStr(ADR('err'), ADR('coord1_not_bound'));
                GVL.Coord1NotBoundNakCount := GVL.Coord1NotBoundNakCount + 1;
                ShouldReturn := TRUE;
                CommandAck := FALSE;
                GVL.minfo_buf_ridx.ConsumeTail();
            ELSE
            MotionPose.c.X := MessageUnpackerFb.TryReadREAL('X', MotionPose.c.X);
            MotionPose.c.Y := MessageUnpackerFb.TryReadREAL('Y', MotionPose.c.Y);
            MotionPose.c.Z := MessageUnpackerFb.TryReadREAL('Z', MotionPose.c.Z);
            // A-axis Kin_CAxis wrap workaround. Constant lives in AxisGroupSM
            // VAR_INPUT (A_AXIS_KIN_WRAP_SCALE) so the comment-with-coupling
            // is at the declaration site, not buried here.
            MotionPoseTempReal := MessageUnpackerFb.TryReadREAL('A', G1_A_UNSET_SENTINEL);
            IF (MotionPoseTempReal <> G1_A_UNSET_SENTINEL) THEN
                MotionPose.c.A := MotionPoseTempReal / A_AXIS_KIN_WRAP_SCALE;
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

            // Phase 4 step 2: frame=1 simply switches CoordSystem to
            // PCS_1. MC_TrackConveyorBelt keeps PCS_1 origin synced to
            // the belt every scan, so MotionPose is interpreted as the
            // tracking-frame target and the kernel handles the
            // synchronized handover during the move (the prior
            // hand-rolled "Coord1OriginNow add at MC call time" was a
            // frozen-target approximation -- replaced by SM3 native FB).
            G1TargetPose := MotionPose;
            IF (G1FrameTag = 1) THEN
                G1CoordSystem := SMC_COORD_SYSTEM.PCS_1;
            ELSE
                G1CoordSystem := SMC_COORD_SYSTEM.WCS;
            END_IF;

            MoveLinearAbsoluteFb(
                AxisGroup := AxisGroupManagerFb._agAxisGroup,
                Execute := 1,
                Position := G1TargetPose,
                Velocity := LatestFeedRate,
                Acceleration := LatestAcceleration,
                Deceleration := LatestDeceleration,
                Jerk := LatestJerk,
                CoordSystem := G1CoordSystem,
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
                GVL.minfo_buf_ridx.ConsumeTail();

                MessagePackerFb.PackKvLint(ADR('movement_id'), TO_LINT(MoveLinearAbsoluteFb.MovementId));

                // PH#6: record this accepted G1 in the dedupe ring so a
                // retransmit with the same CommandId hits the cache and
                // replays the movement_id instead of executing again.
                // Skip the no-id sentinel (CommandId=0) -- caching it
                // would collide every legacy/no-id G1 with each other.
                IF (CommandId <> 0) THEN
                    RecentCmdIds[RecentCmdRingHead] := CommandId;
                    RecentCmdMoveIds[RecentCmdRingHead] := MoveLinearAbsoluteFb.MovementId;
                    RecentCmdRingHead := (RecentCmdRingHead + 1) MOD DUPE_RING_SIZE;
                END_IF;

                ShouldReturn := TRUE;
                CommandAck := TRUE;
            ELSE
                RetryMotionCommandCooldown := RETRY_COOLDOWN_G1_MS;
            END_IF;
            END_IF; // frame validation / MoveLinear
            END_IF; // dedupe / coord-gate / execute

        ELSIF (CommandType = 'getDigitalInputFlipCount') THEN

            MessagePackerFb.PackKvDint(ADR('raw'), TO_DINT(DigitalInputBits_Curr));

            MessagePackerFb.PackKvArray(ADR('fc'), 16);
            FOR StageLoopIdx := 0 TO 15 DO
                MessagePackerFb.PackElemDint(TO_DINT(DigitalInputFlipCounts[StageLoopIdx]));
            END_FOR;

            ShouldReturn := TRUE;
            CommandAck := TRUE;
            GVL.minfo_buf_ridx.ConsumeTail();

        ELSE
            // R2 fix: unknown motion cmd used to NAK bare (no err, no
            // counter) -- host saw ack:false with zero diagnosis.
            MessagePackerFb.PackKvStr(ADR('err'), ADR('unknown_cmd'));
            GVL.UnknownCmdNakCount := GVL.UnknownCmdNakCount + 1;
            GVL.minfo_buf_ridx.ConsumeTail();
            ShouldReturn := TRUE;
        END_IF;

        IF ShouldReturn = TRUE AND CommandId <> 0 THEN
            MessagePackerFb.PackKvDint(ADR('id'), CommandId);

            MessagePackerFb.PackKvBool(ADR('ack'), CommandAck);

            MsgPakInfoWrapup(ResponsePacketPointer^);
            // Only commit the slot if we actually reserved one above.
            // SlotAvailable=FALSE means the pack wrote to the scratch slot.
            IF SlotAvailable THEN
                GVL.reMP_info_ridx.PushHead();
            END_IF;
        END_IF;
    END_IF;
