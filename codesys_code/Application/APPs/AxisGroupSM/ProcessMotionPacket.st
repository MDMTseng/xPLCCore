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
            GVL.minfo_buf_ridx.ConsumeTail();

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
                GVL.minfo_buf_ridx.ConsumeTail();
            ELSIF (BlockTimeoutMs > 0 AND (RuntimeMs - BlockStartMs) >= BlockTimeoutMs) THEN
                ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 1;
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('block_timeout'));
                ShouldReturn := TRUE;
                CommandAck := FALSE;
                GVL.minfo_buf_ridx.ConsumeTail();
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
                GVL.minfo_buf_ridx.ConsumeTail();
            ELSIF (BlockTimeoutMs > 0 AND (RuntimeMs - BlockStartMs) >= BlockTimeoutMs) THEN
                ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 1;
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('block_timeout'));
                ShouldReturn := TRUE;
                CommandAck := FALSE;
                GVL.minfo_buf_ridx.ConsumeTail();
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
            GVL.minfo_buf_ridx.ConsumeTail();
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
            GVL.minfo_buf_ridx.ConsumeTail();

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
                    GVL.minfo_buf_ridx.ConsumeTail();
                    ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 1;

                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('movement_id'));
                    ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := TO_LINT(GroupWaitFb.MovementId));

                    ShouldReturn := TRUE;
                    CommandAck := TRUE;
                ELSE
                    RetryMotionCommandCooldown := RETRY_COOLDOWN_G4_MS;
                END_IF;
            ELSE
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
                ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 1;
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('motion_in_flight'));
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
                ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 1;
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('motion_in_flight'));
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
            GVL.minfo_buf_ridx.ConsumeTail();

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
            GVL.minfo_buf_ridx.ConsumeTail();

        ELSIF (CommandType = 'M4') THEN

				MotionTargetMovementId := TO_ULINT(MessageUnpackerFb.TryReadUINT64('motion_id', TO_ULINT(GroupReadStatusFb.LastAcceptedMovementId)));

				ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 1;

				ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('ev_buf_space'));

				ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := TO_LINT(FlyEventAvailableCount));



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

					END_IF;

					GVL.minfo_buf_ridx.ConsumeTail();

				END_IF;

			

ELSIF (CommandType = 'WAIT_FOR_TRIGGER_MOTION_PROGRESS') THEN
            IF (FlyEventAvailableCount > 3) THEN
					
                MotionTargetMovementId := TO_ULINT(MessageUnpackerFb.TryReadINT64('motion_id', TO_LINT(GroupReadStatusFb.LastAcceptedMovementId)));




                IF (MotionOutputPin <> 0 AND MotionTargetMovementId <> 0) THEN
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
                END_IF;
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
                ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 2;
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('movement_id'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := TO_LINT(RecentCmdMoveIds[DupeMatchIdx]));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('dedup'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackBool(pStart := ResponsePacketPointer^.whead, bValue := TRUE);
                ShouldReturn := TRUE;
                CommandAck := TRUE;
                GVL.DupeCommandCount := GVL.DupeCommandCount + 1;
                GVL.minfo_buf_ridx.ConsumeTail();

            // Post-homing coord-system gate. Refuse G1 motion until the
            // operator has called SetCoord0 or SetCoord1 since the last
            // UnInited entry. Prevents machine-coord crashes when the UI
            // (or a test script) skips the SetCoord call after homing.
            ELSIF (NOT GVL.CoordSystemConfigured) THEN
                ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 1;
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('coord_not_configured'));
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
                ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 1;
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('bad_frame'));
                GVL.Coord1BadFrameNakCount := GVL.Coord1BadFrameNakCount + 1;
                ShouldReturn := TRUE;
                CommandAck := FALSE;
                GVL.minfo_buf_ridx.ConsumeTail();
            ELSIF (G1FrameTag = 1 AND NOT GVL.Coord1Bound) THEN
                ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 1;
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('coord1_not_bound'));
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

                ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 1;
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('movement_id'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := TO_LINT(MoveLinearAbsoluteFb.MovementId));

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
            GVL.minfo_buf_ridx.ConsumeTail();

        ELSE
            GVL.minfo_buf_ridx.ConsumeTail();
            ShouldReturn := TRUE;
        END_IF;

        IF ShouldReturn = TRUE AND CommandId <> 0 THEN
            ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 2;
            ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('id'));
            ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := CommandId);

            ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('ack'));
            ResponsePacketPointer^.whead := MessagePackerFb.PackBool(pStart := ResponsePacketPointer^.whead, bValue := CommandAck);

            MsgPakInfoWrapup(ResponsePacketPointer^);
            // Only commit the slot if we actually reserved one above.
            // SlotAvailable=FALSE means the pack wrote to the scratch slot.
            IF SlotAvailable THEN
                GVL.reMP_info_ridx.PushHead();
            END_IF;
        END_IF;
    END_IF;
