METHOD Update : BOOL
VAR_INPUT
	//-1 enter, 0 update,1 exit current(_eState) state
	uidx:INT:=0;
END_VAR
VAR
	
END_VAR
(* =========== IMPLEMENTATION =========== *)

Update:=TRUE;

// Group power (delta joints + A), every state. It used to be called after
// the CASE, which UnInited leaves with RETURN: in UnInited it was never
// called, so GroupPower_Flag FALSE never reached the axes and they stayed
// powered after a reset (found 2026-09-30 on the delta bench test; only a
// bus restart or a download switched them off). GroupPower_Flag is the
// one set by the previous scan's state; one scan of delay.
SMC_GroupPower_0(
    AxisGroup    := _agAxisGroup,
    Enable       := TRUE,
    bRegulatorOn := GroupPower_Flag,
    bDriveStart  := GroupPower_Flag
);

// Reel power, every state, for the same reason: after the CASE it was
// skipped in UnInited too, so bReelPowerReq FALSE never reached the reel
// and it stayed powered (operation enabled) after a reset (found
// 2026-10-07: reel 0x6041 = 16#1637 in UnInited after a soak).
// bReelPowerReq is the one set by the previous scan's state.
// Enable stays TRUE like SMC_GroupPower's: MC_Power with Enable FALSE just
// stops processing and leaves the drive as it is (it only switches off
// through bRegulatorOn / bDriveStart FALSE while enabled) -- moving the
// call alone did not switch the reel off (2026-10-07 14:43).
fbPowerReel(
    Axis         := _axReel,
    Enable       := TRUE,
    bRegulatorOn := bReelPowerReq,
    bDriveStart  := bReelPowerReq
);

CASE _eState OF
    E_RobotState.UnInited:
        IF(uidx=-1)THEN // On Entry
            GVL.TestReelFrozen := FALSE;   // fault-injection hook (GVL)
            GroupPower_Flag := FALSE;
            bReelPowerReq   := FALSE; // Single Axis Power Flag

            // Was the bus at fault? Decided before the error latch is
            // cleared: only then does this entry restart the EtherCAT bus.
            xBusRestartNeeded := IoConfig_Globals.EtherCAT_Master_SoftMotion.xError
                OR (GVL.LastErrorSource = 'Supervisor:EtherCAT_Master')
                OR GVL.BusRestartOnReset;

            // Clear the error latch so a recovered run starts clean.
            GVL.LastErrorSource := '';
            GVL.LastErrorID     := 0;

            // Reset the coord-system gate: after any reset the frame is
            // applied again (by the PLC on Ready entry, or SetCoord0/1)
            // before G1 motion is accepted.
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

            // Restart the EtherCAT bus only when it was at fault. It used to
            // restart on every reset: each one put the DC out of sync for
            // ~3.4 s (3417 cycles, a 1190 us cycle; the "DC statistics
            // timeouts" the owner saw, machine 2026-09-29) and re-initialised
            // every slave, IO outputs included. GVL.BusRestartOnReset brings
            // the old behaviour back. Throttled: at most once per
            // BUS_RESTART_COOLDOWN_MS, so Error -> UnInited loops do not
            // hammer the master.
            IF xBusRestartNeeded AND ((GVL.LastBusRestartMs = 0) OR
               (AxisGroupSM.RuntimeMs - GVL.LastBusRestartMs >= GVL.BUS_RESTART_COOLDOWN_MS)) THEN
                IoConfig_Globals.EtherCAT_Master_SoftMotion.xRestart := TRUE;
                GVL.LastBusRestartMs := AxisGroupSM.RuntimeMs;
            END_IF
        END_IF

        IF(uidx=0)THEN // On Update
            // 2026-10-05: execute only when there is something to do. Held
            // TRUE, the reset logged "GroupFBError 78: there is no error to
            // reset" every 3 s and the disable "2AF8 wrong state" whenever
            // the group was already disabled; the PLC logger (500 entries,
            // stops when full) was full within minutes of every UnInited,
            // which is why the 2026-10-03/04 dropouts left no log.
            SMC_GroupReset(AxisGroup:=_agAxisGroup,
                           Execute:=AxisGroupSM.GroupReadStatusFb.GroupErrorStop);
            SMC_GroupDisable(AxisGroup:=_agAxisGroup,
                             Execute:=NOT AxisGroupSM.GroupReadStatusFb.GroupDisabled
                                      AND NOT AxisGroupSM.GroupReadStatusFb.GroupErrorStop);

            // Reset the single motor only while it is in errorstop
            fbResetReel(Axis:= _axReel, Execute:=(_axReel.nAxisState = SMC_AXIS_STATE.errorstop));

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
                TripPowerError(ADR('Powering'));
            ELSIF watchdogPowering.Q THEN
                TripTimeout(ADR('Powering'));
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
                TripPowerError(ADR('Powered'));
            END_IF
        END_IF

    E_RobotState.GroupEnabling:
        IF(uidx=-1)THEN
            enable_fb(AxisGroup:=_agAxisGroup, Execute:=FALSE);
            watchdogGroupEnabling(IN := FALSE, PT := WATCHDOG_GROUP_ENABLE_PT);
        ELSIF(uidx=0)THEN
            enable_fb(AxisGroup:=_agAxisGroup, Execute:=TRUE);
            watchdogGroupEnabling(IN := TRUE, PT := WATCHDOG_GROUP_ENABLE_PT);

            // Single motor is already enabled via MC_Power in previous state.
            // enable_fb.Error is its own source -- TripPowerError doesn't fit
            // (not a power FB); stamp inline. Watchdog uses the shared helper.
            IF(enable_fb.Done) THEN
                Transition(E_RobotEvent.EV_OK);
            ELSIF(enable_fb.Error) THEN
                GVL.LastErrorSource := 'GroupEnabling:enable_fb';
                GVL.LastErrorID := TO_DWORD(enable_fb.ErrorID);
                Transition(E_RobotEvent.EV_ERROR);
            ELSIF watchdogGroupEnabling.Q THEN
                TripTimeout(ADR('GroupEnabling'));
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
                TripPowerError(ADR('GroupEnabled'));
            END_IF
        END_IF

    E_RobotState.Homing:
        IF(uidx=-1)THEN
            homing_fb(bExecute:=FALSE);
            HomingDoneDebounceCD := 100;
            watchdogHoming(IN := FALSE, PT := WATCHDOG_HOMING_PT);
        ELSIF(uidx=0)THEN
            GroupPower_Flag := TRUE;
            bReelPowerReq   := TRUE;
            homing_fb(bExecute:=TRUE);
            watchdogHoming(IN := TRUE, PT := WATCHDOG_HOMING_PT);
            IF(homing_fb.bDone) THEN
                HomingDoneDebounceCD := HomingDoneDebounceCD - 1;
                IF(HomingDoneDebounceCD = 0)THEN
                    Transition(E_RobotEvent.EV_OK);
                END_IF
            ELSIF(homing_fb.bError)THEN
                GVL.LastErrorSource := 'Homing:homing_fb';
                GVL.LastErrorID := 0;
                Transition(E_RobotEvent.EV_ERROR);
            ELSIF watchdogHoming.Q THEN
                TripTimeout(ADR('Homing'));
            ELSE
                HomingDoneDebounceCD := 100;
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
            // and is NAKed. Hold Execute=TRUE for ReadyResetHoldCD scans
            // then drop.
            SMC_GroupReset(AxisGroup:=_agAxisGroup, Execute:=FALSE);
            ReadyResetHoldCD := 50;
        ELSIF(uidx=0)THEN
            GroupPower_Flag := TRUE;
            bReelPowerReq   := TRUE;
            IF ReadyResetHoldCD > 0 THEN
                SMC_GroupReset(AxisGroup:=_agAxisGroup, Execute:=TRUE);
                ReadyResetHoldCD := ReadyResetHoldCD - 1;
            ELSE
                SMC_GroupReset(AxisGroup:=_agAxisGroup, Execute:=FALSE);
            END_IF
            IF(SMC_GroupPower_0.Error OR fbPowerReel.Error) THEN
                TripPowerError(ADR('Ready'));
            END_IF
        END_IF
        
    E_RobotState.Error:
        // The reel stays powered while a tape move is under way
        // (GVL.ReelMoveInFlight): cutting it mid-move leaves the tape
        // between two cells. Its own fault or an E-stop stops it anyway;
        // the tracker then closes the move and the power drops.
        IF(uidx=-1)THEN
            GroupPower_Flag := FALSE;
            bReelPowerReq   := GVL.ReelMoveInFlight;
            // Fault record (2026-10-05): kept past the reset to UnInited.
            GVL.FaultSeq    := GVL.FaultSeq + 1;
            GVL.FaultSource := GVL.LastErrorSource;
            GVL.FaultId     := GVL.LastErrorID;
            GVL.FaultAtMs   := AxisGroupSM.RuntimeMs;
            GVL.MaintArmUntilMs := 0;      // an Error ends maintenance

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
            bReelPowerReq   := GVL.ReelMoveInFlight;
            SMC_GroupDisable(AxisGroup:=_agAxisGroup, Execute:=TRUE);
            SMC_GroupReset(AxisGroup:=_agAxisGroup, Execute:=TRUE);
            fbResetReel(Axis:= _axReel, Execute:=NOT GVL.ReelMoveInFlight);
        END_IF
END_CASE

// --- CALL FUNCTION BLOCKS OUTSIDE THE CASE ---

// 1. Robot Group Power: called before the CASE (see there), every state.

// 2. Single Motor Power (Reel Pull): called before the CASE (see there), every state.

// 3. Single Motor Reset (tick every scan so Done/Busy/Error advance)
fbResetReel(Axis := _axReel);
