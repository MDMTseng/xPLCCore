METHOD Update : BOOL
VAR_INPUT
	//-1 enter, 0 update,1 exit current(_eState) state
	uidx:INT:=0;
END_VAR
VAR
	
END_VAR
(* =========== IMPLEMENTATION =========== *)

Update:=TRUE;

CASE _eState OF
    E_RobotState.UnInited:
        IF(uidx=-1)THEN // On Entry
            GroupPower_Flag := FALSE;
            bReelPowerReq   := FALSE; // Single Axis Power Flag

            // Clear the error latch so a recovered run starts clean.
            GVL.LastErrorSource := '';
            GVL.LastErrorID     := 0;

            // Reset the coord-system gate: after any reset, operator must
            // re-call SetCoord0/SetCoord1 before G1 motion is accepted.
            GVL.CoordSystemConfigured := FALSE;

            // 2026-06-23 code-review fix: clear COORD1 tracking state on
            // UnInited entry too. Pre-fix, EV_RESET while a COORD1_BIND
            // was active looped FSM back through UnInited but left
            // Coord1Bound=TRUE with the previous cycle's PCS_1 transform
            // alive. The next frame=1 G1 then passed the
            // ProcessMotionPacket Coord1Bound gate against stale
            // calibration; subsequent COORD1_BIND was rejected with
            // 'rebind_requires_unbind' and the operator couldn't recover
            // without an explicit SYS COORD1_UNBIND.
            GVL.Coord1Bound       := FALSE;
            GVL.Coord1ExitPulse   := -1;
            GVL.Coord1BindEventId := 0;

            // Reset Group FBs
            SMC_GroupDisable(AxisGroup:=_agAxisGroup, Execute:=FALSE);
            SMC_GroupReset(AxisGroup:=_agAxisGroup, Execute:=FALSE);

            // Reset Single Axis FBs
            fbResetReel(Axis:= _axReel, Execute:=FALSE);

            // Throttle bus restart: pulse xRestart only when the cooldown
            // has elapsed since the last pulse. Prevents Error -> UnInited
            // loops from hammering the EtherCAT master. AxisGroupSM.RuntimeMs
            // is the PLC-scan monotonic millisecond counter.
            IF (GVL.LastBusRestartMs = 0) OR
               (AxisGroupSM.RuntimeMs - GVL.LastBusRestartMs >= GVL.BUS_RESTART_COOLDOWN_MS) THEN
                IoConfig_Globals.EtherCAT_Master_SoftMotion.xRestart := TRUE;
                GVL.LastBusRestartMs := AxisGroupSM.RuntimeMs;
            END_IF
        END_IF

        IF(uidx=0)THEN // On Update
            SMC_GroupDisable(AxisGroup:=_agAxisGroup, Execute:=TRUE);
            SMC_GroupReset(AxisGroup:=_agAxisGroup, Execute:=TRUE);

            // Reset the single motor
            fbResetReel(Axis:= _axReel, Execute:=TRUE);

            IoConfig_Globals.EtherCAT_Master_SoftMotion();
            IoConfig_Globals.EtherCAT_Master_SoftMotion.xRestart:=FALSE;
        END_IF
        RETURN;

    E_RobotState.Powering:
        IF(uidx=-1)THEN
            GroupPower_Flag := FALSE;
            bReelPowerReq   := FALSE;
            watchdogPowering(IN := FALSE, PT := WATCHDOG_POWERING_PT);
        ELSIF(uidx=0)THEN
            GroupPower_Flag := TRUE;
            bReelPowerReq   := TRUE;
            watchdogPowering(IN := TRUE, PT := WATCHDOG_POWERING_PT);

            // Wait for BOTH the group and the reel motor to report powered
            IF(SMC_GroupPower_0.Status AND fbPowerReel.Status) THEN
                Transition(E_RobotEvent.EV_OK);
            ELSIF(SMC_GroupPower_0.Error OR fbPowerReel.Error) THEN
                IF SMC_GroupPower_0.Error THEN
                    GVL.LastErrorSource := 'Powering:SMC_GroupPower';
                    GVL.LastErrorID := TO_DWORD(SMC_GroupPower_0.ErrorID);
                ELSE
                    GVL.LastErrorSource := 'Powering:fbPowerReel';
                    GVL.LastErrorID := TO_DWORD(fbPowerReel.ErrorID);
                END_IF
                Transition(E_RobotEvent.EV_ERROR);
            ELSIF watchdogPowering.Q THEN
                GVL.LastErrorSource := 'Powering:timeout';
                GVL.LastErrorID := 0;
                Transition(E_RobotEvent.EV_ERROR);
            END_IF
        END_IF

    E_RobotState.Powered:
        IF(uidx=-1)THEN
            GroupPower_Flag := TRUE;
            bReelPowerReq   := TRUE;
        ELSIF(uidx=0)THEN
            // Keep both drives alive while waiting for group enable command
            GroupPower_Flag := TRUE;
            bReelPowerReq   := TRUE;
            IF(SMC_GroupPower_0.Error OR fbPowerReel.Error) THEN
                IF SMC_GroupPower_0.Error THEN
                    GVL.LastErrorSource := 'Powered:SMC_GroupPower';
                    GVL.LastErrorID := TO_DWORD(SMC_GroupPower_0.ErrorID);
                ELSE
                    GVL.LastErrorSource := 'Powered:fbPowerReel';
                    GVL.LastErrorID := TO_DWORD(fbPowerReel.ErrorID);
                END_IF
                Transition(E_RobotEvent.EV_ERROR);
            END_IF
        END_IF

    E_RobotState.GroupEnabling:
        IF(uidx=-1)THEN
            enable_fb(AxisGroup:=_agAxisGroup, Execute:=FALSE);
            watchdogGroupEnabling(IN := FALSE, PT := WATCHDOG_GROUP_ENABLE_PT);
        ELSIF(uidx=0)THEN
            enable_fb(AxisGroup:=_agAxisGroup, Execute:=TRUE);
            watchdogGroupEnabling(IN := TRUE, PT := WATCHDOG_GROUP_ENABLE_PT);

            // Single motor is already enabled via MC_Power in previous state
            IF(enable_fb.Done) THEN
                Transition(E_RobotEvent.EV_OK);
            ELSIF(enable_fb.Error) THEN
                GVL.LastErrorSource := 'GroupEnabling:enable_fb';
                GVL.LastErrorID := TO_DWORD(enable_fb.ErrorID);
                Transition(E_RobotEvent.EV_ERROR);
            ELSIF watchdogGroupEnabling.Q THEN
                GVL.LastErrorSource := 'GroupEnabling:timeout';
                GVL.LastErrorID := 0;
                Transition(E_RobotEvent.EV_ERROR);
            END_IF
        END_IF

    E_RobotState.GroupEnabled:
        IF(uidx=-1)THEN
            GroupPower_Flag := TRUE;
            bReelPowerReq   := TRUE;
        ELSIF(uidx=0)THEN
            // Group is enabled, keep power asserted for normal operation
            GroupPower_Flag := TRUE;
            bReelPowerReq   := TRUE;
            IF(SMC_GroupPower_0.Error OR fbPowerReel.Error) THEN
                IF SMC_GroupPower_0.Error THEN
                    GVL.LastErrorSource := 'GroupEnabled:SMC_GroupPower';
                    GVL.LastErrorID := TO_DWORD(SMC_GroupPower_0.ErrorID);
                ELSE
                    GVL.LastErrorSource := 'GroupEnabled:fbPowerReel';
                    GVL.LastErrorID := TO_DWORD(fbPowerReel.ErrorID);
                END_IF
                Transition(E_RobotEvent.EV_ERROR);
            END_IF
        END_IF

    E_RobotState.Homing:
        IF(uidx=-1)THEN
            homing_fb(bExecute:=FALSE);
            stateCD:=100;
            watchdogHoming(IN := FALSE, PT := WATCHDOG_HOMING_PT);
        ELSIF(uidx=0)THEN
            GroupPower_Flag := TRUE;
            bReelPowerReq   := TRUE;
            homing_fb(bExecute:=TRUE);
            watchdogHoming(IN := TRUE, PT := WATCHDOG_HOMING_PT);
            IF(homing_fb.bDone) THEN
                stateCD:=stateCD-1;
                IF(stateCD=0)THEN
                    Transition(E_RobotEvent.EV_OK);
                END_IF
            ELSIF(homing_fb.bError)THEN
                GVL.LastErrorSource := 'Homing:homing_fb';
                GVL.LastErrorID := 0;
                Transition(E_RobotEvent.EV_ERROR);
            ELSIF watchdogHoming.Q THEN
                GVL.LastErrorSource := 'Homing:timeout';
                GVL.LastErrorID := 0;
                Transition(E_RobotEvent.EV_ERROR);
            ELSE
                stateCD:=100;
            END_IF
        ELSIF(uidx=1)THEN
            homing_fb(bExecute:=FALSE);
            watchdogHoming(IN := FALSE, PT := WATCHDOG_HOMING_PT);
        END_IF

    E_RobotState.Ready:
        IF(uidx=-1)THEN
            GroupPower_Flag := TRUE;
            bReelPowerReq   := TRUE;
            // Per-axis SMC_Homing (in FB_Homing) leaves the axis group in
            // ErrorStop once axes return to group control. Without a reset
            // pulse here, the very first motion packet after homing hits
            // the GroupErrorStop gate in AxisGroupSM.ProcessMotionPacket
            // and is NAKed. Hold Execute=TRUE for stateCD scans then drop.
            SMC_GroupReset(AxisGroup:=_agAxisGroup, Execute:=FALSE);
            stateCD := 50;
        ELSIF(uidx=0)THEN
            GroupPower_Flag := TRUE;
            bReelPowerReq   := TRUE;
            IF stateCD > 0 THEN
                SMC_GroupReset(AxisGroup:=_agAxisGroup, Execute:=TRUE);
                stateCD := stateCD - 1;
            ELSE
                SMC_GroupReset(AxisGroup:=_agAxisGroup, Execute:=FALSE);
            END_IF
            IF(SMC_GroupPower_0.Error OR fbPowerReel.Error) THEN
                IF SMC_GroupPower_0.Error THEN
                    GVL.LastErrorSource := 'Ready:SMC_GroupPower';
                    GVL.LastErrorID := TO_DWORD(SMC_GroupPower_0.ErrorID);
                ELSE
                    GVL.LastErrorSource := 'Ready:fbPowerReel';
                    GVL.LastErrorID := TO_DWORD(fbPowerReel.ErrorID);
                END_IF
                Transition(E_RobotEvent.EV_ERROR);
            END_IF
        END_IF
        
    E_RobotState.Error:
        IF(uidx=-1)THEN
            GroupPower_Flag := FALSE;
            bReelPowerReq   := FALSE;

            // PH#6: clear the coord-system gate on Error entry too, not
            // just on UnInited. Without this, a Ready -> Error -> Ready
            // recovery (group reset by SMC, no operator EV_RESET) leaves
            // CoordSystemConfigured TRUE and lets G1 fire against stale
            // homing/coord state. UnInited entry still clears it for the
            // full reset path; this covers the in-place recovery case.
            GVL.CoordSystemConfigured := FALSE;

            SMC_GroupDisable(AxisGroup:=_agAxisGroup, Execute:=FALSE);
            SMC_GroupReset(AxisGroup:=_agAxisGroup, Execute:=FALSE);
            fbResetReel(Axis:= _axReel, Execute:=FALSE);
        END_IF
        
        IF(uidx=0)THEN
            // Hold power commands low every scan while in Error
            GroupPower_Flag := FALSE;
            bReelPowerReq   := FALSE;
            SMC_GroupDisable(AxisGroup:=_agAxisGroup, Execute:=TRUE);
            SMC_GroupReset(AxisGroup:=_agAxisGroup, Execute:=TRUE);
            fbResetReel(Axis:= _axReel, Execute:=TRUE);
        END_IF
END_CASE

// --- CALL FUNCTION BLOCKS OUTSIDE THE CASE ---

// 1. Robot Group Power
SMC_GroupPower_0(
    AxisGroup    := _agAxisGroup,
    Enable       := TRUE,
    bRegulatorOn := GroupPower_Flag,
    bDriveStart  := GroupPower_Flag
);

// 2. Single Motor Power (Reel Pull)
fbPowerReel(
    Axis         := _axReel,
    Enable       := bReelPowerReq,
    bRegulatorOn := bReelPowerReq,
    bDriveStart  := bReelPowerReq
);

// 3. Single Motor Reset (tick every scan so Done/Busy/Error advance)
fbResetReel(Axis := _axReel);
