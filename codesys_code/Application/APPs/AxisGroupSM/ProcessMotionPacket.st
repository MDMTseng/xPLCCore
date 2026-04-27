(* =========== IMPLEMENTATION =========== *)

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
