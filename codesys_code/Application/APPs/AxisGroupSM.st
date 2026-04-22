
PROGRAM AxisGroupSM
    VAR CONSTANT
        FlyEventBufferSize: UDINT := 10;
        TaskInstructionBufferSize: UDINT := 10;
        FlyEventStageLimit: INT := 9;
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


















    Index : INT;
    StageLoopIdx : INT;
    PinOpSeqIdx : INT; // Index for iterating through pin_op_seq array
    
    TempMovementId : SMC_Movement_Id;
    
    MessageBufferPointer : POINTER TO BYTE;
    AuxCommandBufferPointer : POINTER TO BYTE;

    
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


    DBG_BLOCK : BOOL:=TRUE;

    END_VAR
(* =========== IMPLEMENTATION =========== *)

//IMPORTANT:following if TRUE... and if FALSE.... are mandatory, DO not try to remove/optimize it


    //InitRuntime();
    IF TRUE THEN
        IF RuntimeMs = 0 THEN
            FOR Index := 0 TO GVL.reMP_info_ridx.capacity() - 1 DO
                GVL.reMP_info_arr[Index] := MsgPakInfoInit(
                    buff := ADR(GVL.reMP_info_buf[Index]),
                    buffer_size := GVL.reMP_info_buff_SIZE,
                    useLargeElementCountField := TRUE
                );
            END_FOR
        END_IF;
    END_IF;


    //UpdateRuntimeAndInputEvent();
    IF TRUE THEN
        RuntimeMs := RuntimeMs + BusPeriod;

        CASE VisuEventControl OF
            0: InputEvent := E_RobotEvent.EV_POWER_ON;
            1: InputEvent := E_RobotEvent.EV_GROUP_ENABLE;
            2: InputEvent := E_RobotEvent.EV_HOME_GO;
            3: InputEvent := E_RobotEvent.EV_ERROR;
            4: InputEvent := E_RobotEvent.EV_RESET;
            5: InputEvent := E_RobotEvent.EV_HOME_GO_FORCE_SKIP;
        END_CASE;

        IF (PreviousInputEvent <> InputEvent) THEN
            AxisGroupManagerFb.TRANSITION(
                eEvent := InputEvent
            );
        END_IF;

        PreviousInputEvent := InputEvent;
        IF TO_INT(AxisGroupManagerFb._eState) <> PrevAxisGroupState THEN
            StateString := TO_STRING(AxisGroupManagerFb._eState);
            PrevAxisGroupState := TO_INT(AxisGroupManagerFb._eState);
        END_IF;
    END_IF;


    //UpdateAxisGroupState();
    IF TRUE THEN
        AxisGroupManagerFb.Update();

        GroupReadStatusFb(
            AxisGroup := AxisGroupManagerFb._agAxisGroup,
            Enable := 1,
            ActiveMovementId =>
        );
		

        GroupReadPositionFb(
            AxisGroup := AxisGroupManagerFb._agAxisGroup,
            Enable := TRUE
        );
       

        GroupActualPositionFb(
            AxisGroup := AxisGroupManagerFb._agAxisGroup,
            Enable := 1,
            CoordSystem := SMC_COORD_SYSTEM.WCS,
            Position => ReadPosition,
        );
		
    END_IF;


    //UpdateMotionProgress();
    IF TRUE THEN
        TempMovementId := GroupReadPositionFb.MovementId;
        IF (GroupReadPositionFb.MovementId = 0) THEN
            MotionBufferSize := 0;
            CurrentMovementId := GroupReadStatusFb.LastAcceptedMovementId;
            CurrentMovementIdProgress := 1;
            IF (CurrentMovementId = 0) THEN
                CurrentMovementIdProgress := 0;
            END_IF;
        ELSE
            MotionBufferSize := GroupReadStatusFb.LastAcceptedMovementId - GroupReadPositionFb.MovementId;

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

        ShouldReturn := FALSE;
        CommandAck := FALSE;

    END_IF;


    //ReadIncomingPacket();
    IF TRUE THEN
        PacketType := '';
        ResponsePacketPointer := 0;

        IF (GVL.minfo_buf_ridx.size() > 0) THEN
            MessageBufferPointer := ADR(GVL.minfo_buf[GVL.minfo_buf_ridx.getTail()][0]);
            MessageUnpackerFb.Init(ADR(MessageBufferPointer[1]), MessageBufferPointer[0]);
            CommandId := TO_DINT(MessageUnpackerFb.TryReadINT64('id', - 1));
            PacketType := MessageUnpackerFb.TryReadString('type', '');
            CommandType := MessageUnpackerFb.TryReadString('cmd', '');
        END_IF;
    END_IF;

    IF TRUE THEN//ProcessSystemPacket

        
        IF (PacketType = 'SYS') THEN
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
                ResponsePacketPointer^.ele_count := 2;
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('st'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := AxisGroupManagerFb._eState);
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('st_str'));
                StateString := TO_STRING(AxisGroupManagerFb._eState);
                PrevAxisGroupState := TO_INT(AxisGroupManagerFb._eState);
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR(StateString));
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


    IF TRUE THEN//ProcessAuxCommands
        IF (GVL.aux0_info_buf_ridx.size() > 0) THEN
            AuxCommandBufferPointer := ADR(GVL.aux0_info_buf[GVL.aux0_info_buf_ridx.getTail()][0]);
            // TODO: parse/handle AUX thread 0 data from AuxCommandBufferPointer
            GVL.aux0_info_buf_ridx.consumeTail();
        END_IF;

        IF (GVL.aux1_info_buf_ridx.size() > 0) THEN
            AuxCommandBufferPointer := ADR(GVL.aux1_info_buf[GVL.aux1_info_buf_ridx.getTail()][0]);
            // TODO: parse/handle AUX thread 1 data from AuxCommandBufferPointer
            GVL.aux1_info_buf_ridx.consumeTail();
        END_IF;

        IF (GVL.aux2_info_buf_ridx.size() > 0) THEN
            AuxCommandBufferPointer := ADR(GVL.aux2_info_buf[GVL.aux2_info_buf_ridx.getTail()][0]);
            // TODO: parse/handle AUX thread 2 data from AuxCommandBufferPointer
            GVL.aux2_info_buf_ridx.consumeTail();
        END_IF;
    END_IF;
	
	
	
	
	
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
            GVL.minfo_buf_ridx.clear();

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
                                TriggerErrorCode := 100;
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



        SetCoordTransformFb(
            AxisGroup := AxisGroupManagerFb._agAxisGroup,
            Execute := ApplyCoordTransform,
            CoordTransform := CoordTransform,
            CoordSystem := SMC_COORD_SYSTEM.MCS
        );

        IF ApplyCoordTransform AND (SetCoordTransformFb.Busy OR SetCoordTransformFb.Done OR SetCoordTransformFb.Error) THEN
            ApplyCoordTransform := FALSE;
        END_IF;

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

        IF (RetryMotionCommandCooldown = 0 AND PacketType = 'M' AND GVL.minfo_buf_ridx.size() > 0 AND MotionBufferSize < 12) THEN
            ShouldReturn := FALSE;
            CommandAck := FALSE;

            ResponsePacketPointer := ADR(GVL.reMP_info_arr[GVL.reMP_info_ridx.getHead()]); // prepare for return data buffer
            MsgPakInfoClear(ResponsePacketPointer^);

            IF (GroupReadStatusFb.Error = TRUE OR GroupReadStatusFb.GroupErrorStop) THEN
                ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 1;
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('ERROR_CODE'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := GroupReadStatusFb.ErrorID);

                IF (GroupReadStatusFb.GroupErrorStop) THEN
                    ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 1;
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('MISC'));
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('GroupErrorStop'));
                END_IF;

                ShouldReturn := TRUE;
                CommandAck := FALSE;
                GVL.minfo_buf_ridx.consumeTail();

            ELSIF (CommandType = 'BLOCK_FOR_MOTION_STOP') THEN
                IF (GroupReadPositionFb.MovementId = 0) THEN
                    ShouldReturn := TRUE;
                    CommandAck := TRUE;

                    GVL.minfo_buf_ridx.consumeTail();
                END_IF;

            ELSIF (CommandType = 'BLOCK_FOR_DIGITAL_INPUT') THEN
                InputPinMask := TO_UINT(MessageUnpackerFb.TryReadINT64('pin', 0));
                InputPinGroup := TO_UINT(MessageUnpackerFb.TryReadINT64('group', 0));
                InputPinState := TO_UINT(MessageUnpackerFb.TryReadINT64('state', 0));

                IF DigitalInputPointer <> 0 AND ((DigitalInputPointer[InputPinGroup] AND InputPinMask) = (InputPinState AND InputPinMask)) THEN
                    ShouldReturn := TRUE;
                    CommandAck := TRUE;

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
                        RetryMotionCommandCooldown := 100;
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

                ShouldReturn := TRUE;
                CommandAck := TRUE;
                GVL.minfo_buf_ridx.consumeTail();

            ELSIF (CommandType = 'RESET_DBG_INFO') THEN
                IoTriggerCount := 0;
                IoTriggerResetCount := 0;
                IoTriggerInfoCount := 0;
                IoCommandCount := 0;

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
                MotionPose.c.X := MessageUnpackerFb.TryReadREAL('X', MotionPose.c.X);
                MotionPose.c.Y := MessageUnpackerFb.TryReadREAL('Y', MotionPose.c.Y);
                MotionPose.c.Z := MessageUnpackerFb.TryReadREAL('Z', MotionPose.c.Z);
                MotionPoseTempReal := MessageUnpackerFb.TryReadREAL('A', -919191);
                IF (MotionPoseTempReal <> -919191) THEN
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
                    RetryMotionCommandCooldown := 101;
                END_IF;

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