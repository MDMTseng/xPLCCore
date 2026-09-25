METHOD DrainHostPackets : BOOL
VAR
    // Scratch raw-reads for SCRATCHPAD_WRITE field-presence detection
    // (code review fix #3). -1 sentinel from TryReadINT64 = missing key.
    // Moved here from PROGRAM AxisGroupSM VAR scope 2026-06-23: only
    // used inside this method's SCRATCHPAD_WRITE branch.
    ScratchpadPlanIdRaw          : LINT;
    ScratchpadPlanIndexRaw       : LINT;
    ScratchpadIntentKindRaw      : LINT;
    ScratchpadIntentMidRaw       : LINT;
    ScratchpadLastVisionPulseRaw : LINT;

    // GA_EV ev field raw read (code review fix #7). Same scope reasoning.
    GaEvRaw : LINT;

    // SET_AXIS_SIM mask raw read. -1 sentinel = missing key.
    AxisSimMaskRaw : LINT;

    // EVT_MARK: event-log slot being written.
    EvSlot : UDINT;

    // PLAN_SET / PLAN_GET scratch.
    PlanIdx   : INT;
    PlanN     : INT;
    PlanOkIn  : BOOL;
    PlanNew   : ARRAY[0..31] OF DINT;
    PlanCellsDoneIn : LINT;

    // DI_WATCH scratch.
    DiIdx      : INT;
    DiMaskIn   : WORD;
    DiEdgeIn   : LINT;
    DiThrIn    : LINT;

    // REEL_RESUME / REEL_CLEAR scratch.
    ReelRestMm : LREAL;
    ReelPosDelta : LREAL;
    ReelClearN : DINT;

    // TRUE while the packet being handled came from GVL.sysinfo_buf
    // (order-free SYS ring), FALSE for GVL.minfo_buf.
    SrcSys : BOOL;

    // COORD1_BIND wire-read staging (B3, analysis 2.1). Pre-fix the
    // handler read straight into GVL.Coord1RefPulse/RefXyz/Scale and
    // validated afterwards -- a REJECTED bind still stomped the GVL
    // calibration fields. Stage locally; Coord1CommitBind writes GVL
    // only on the accept path.
    BindRefPulseW : DINT;
    BindRefXW     : LREAL;
    BindRefYW     : LREAL;
    BindRefZW     : LREAL;
    BindScaleXW   : LREAL;
    BindScaleYW   : LREAL;
    BindScaleZW   : LREAL;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Drains the host->PLC packet rings: GVL.sysinfo_buf (order-free SYS,
// always first, never blocked) then GVL.minfo_buf (FIFO). Per-iteration:
//   - protocol_version_mismatch -> NAK + consume + drain next
//   - PacketType='SYS' -> dispatch to GA_EV / PING / GET_MACHINE_STATE /
//     SCRATCHPAD_WRITE / RESET_DBG_INFO / VERSION / GET_DIAG /
//     COORD1_BIND / COORD1_UNBIND / GET_COORD1_DEBUG
//   - PacketType <> 'M' -> missing_type_field NAK
//   - PacketType = 'M' AND NOT Ready -> group_not_ready NAK
//   - PacketType = 'M' AND Ready -> EXIT (leave tail for ProcessMotionPacket)
// Bounded by SYS_DRAIN_MAX_PER_SCAN so a burst can't monopolise the task.
//
// Method returns TRUE unconditionally; the BOOL signature exists so this
// can be called as a statement in the main cyclic body.
DrainHostPackets := TRUE;

// Reset per-scan scratch state. SysDrainIter is a PROGRAM VAR (carries
// across scans); without resetting it here the previous scan's terminal
// value (the SYS_DRAIN_MAX_PER_SCAN cap once a burst hits it) would
// short-circuit the WHILE on subsequent scans. Pre-extract this reset
// lived at the caller site immediately before the WHILE.
PacketType := '';
CommandType := '';
ResponsePacketPointer := 0;
SysDrainIter := 0;

// Any packet received -- not just SYS/PING -- proves the client is alive
// (a UI streaming G1 without PING must not trip the A3 supervisor). Keyed
// on the receive counter, NOT on drain-loop entries: a packet waiting at
// the motion head is re-read every scan and used to keep a dead host
// looking alive (review 2026-09-24 P0-3). The PING handler below still
// refreshes too, alongside the PingMaxGapMs / UiPingCount stats it owns.
IF GVL.HostRxCount <> HostRxCountSeen THEN
    HostRxCountSeen := GVL.HostRxCount;
    GVL.LastUiPingMs := RuntimeMs;
END_IF;

    WHILE ((GVL.sysinfo_buf_ridx.Size() > 0) OR (GVL.minfo_buf_ridx.Size() > 0))
          AND (SysDrainIter < SYS_DRAIN_MAX_PER_SCAN) DO
        SrcSys := GVL.sysinfo_buf_ridx.Size() > 0;
        IF SrcSys THEN
            MessageBufferPointer := ADR(GVL.sysinfo_buf[GVL.sysinfo_buf_ridx.GetTail()][0]);
        ELSE
            MessageBufferPointer := ADR(GVL.minfo_buf[GVL.minfo_buf_ridx.GetTail()][0]);
        END_IF;
        MessageUnpackerFb.Init(ADR(MessageBufferPointer[1]), MessageBufferPointer[0]);
        // R6 fix: missing-id default is 0, matching the no-id sentinel the
        // G1 dedupe ring and the M-path reply gate test against
        // (CommandId <> 0). The old -1 default was cached and matched like
        // a real id: two consecutive id-less G1s -> the second one
        // false-hit the dedupe cache and the motion was silently skipped.
        CommandId := TO_DINT(MessageUnpackerFb.TryReadINT64('id', 0));
        PacketType := MessageUnpackerFb.TryReadString('type', '');
        CommandType := MessageUnpackerFb.TryReadString('cmd', '');
        ProtocolVersionGot := MessageUnpackerFb.TryReadINT64('protocol_version', 0);


        // W1 A5: if the client declared a version and it doesn't match,
        // NAK before any dispatch. 0 (absent / legacy) is allowed through.
        IF (ProtocolVersionGot <> 0 AND TO_UDINT(ProtocolVersionGot) <> GVL.PROTOCOL_VERSION) THEN
            // Drop-newest on overflow (see UpdateMotionProgress). The
            // minfo_buf ConsumeTail and counter still run so a full reply
            // ring doesn't infinitely re-NAK the same packet.
            IF TryAcquireReplySlot(ADR(GVL.ReMpDropCount_Reply)) THEN
                MessagePackerFb.PackKvStr(ADR('err'), ADR('protocol_version_mismatch'));
                MessagePackerFb.PackKvLint(ADR('err_got'), ProtocolVersionGot);
                CommitReplySlotAck(CommandId, FALSE);
            END_IF;

            IF SrcSys THEN GVL.sysinfo_buf_ridx.ConsumeTail(); ELSE GVL.minfo_buf_ridx.ConsumeTail(); END_IF;
            GVL.ProtocolVersionMismatchCount := GVL.ProtocolVersionMismatchCount + 1;

            // Neutralise downstream dispatch in case loop exits next.
            PacketType := '';
            CommandType := '';
            ResponsePacketPointer := 0;
            SysDrainIter := SysDrainIter + 1;

        ELSIF (PacketType = 'SYS') THEN
            // SYS reply lifecycle: TryAcquireReplySlotOrScratch reserves
            // a real slot when possible OR routes into GVL.reMP_info_scratch
            // when the ring is full (so the long ELSIF dispatch chain
            // below can pack unconditionally). CommitReplySlotOrScratch
            // at the bottom appends the id+ack tail, runs Wrapup, and
            // either PushHeads the real slot OR bumps the drop counter
            // on the scratch path -- gated on SlotAvailable that
            // TryAcquireReplySlotOrScratch sets.
            TryAcquireReplySlotOrScratch(ADR(GVL.ReMpDropCount_Reply));

            IF (CommandType = 'GA_EV') THEN
                ShouldReturn := TRUE;
                GaEvRaw := MessageUnpackerFb.TryReadINT64('ev', -1);
                // R1 fix (test_plc_refactor_findings): membership check,
                // not a range check. E_RobotEvent is SPARSE -- 1/3/5 are
                // tombstoned gaps and 10/11 (EV_OK/EV_ER) are internal
                // handshake events; the old 1..11 range let a host post
                // ev=10 and skip a state's hardware handshake, or post a
                // gap value and get ack=TRUE for a guaranteed no-op.
                // Host-postable events are exactly: EV_POWER_ON=2,
                // EV_GROUP_ENABLE=4, EV_HOME_GO=6, EV_HOME_GO_FORCE_SKIP=7,
                // EV_RESET=8, EV_ERROR=9. EV_NONE=0 is also accepted, as a
                // NO-OP STATE POLL (see below). Everything else -- the
                // sparse gaps 1/3/5, the internal EV_OK=10/EV_ER=11, and
                // the -1 missing-key sentinel -- NAKs err='unknown_event'.
                IF NOT (GaEvRaw = 0 OR GaEvRaw = 2 OR GaEvRaw = 4 OR GaEvRaw = 6
                        OR GaEvRaw = 7 OR GaEvRaw = 8 OR GaEvRaw = 9) THEN
                    CommandAck := FALSE;
                    GVL.UnknownEventNakCount := GVL.UnknownEventNakCount + 1;
                    ResponsePacketPointer^.ele_count := 0;
                    // R9 fix: do NOT pack 'ack' here -- CommitReplySlotOrScratch
                    // appends id+ack from CommandAck; packing it here too
                    // produced a duplicate 'ack' key in one msgpack map.
                    MessagePackerFb.PackKvStr(ADR('err'), ADR('unknown_event'));
                ELSE
                    CommandAck := TRUE;
                    // EV_NONE (0) is a no-op state poll: reply with the
                    // current state WITHOUT posting a transition. The
                    // renderer's init_plc_motion loop (OperationPage /
                    // MiscControlsPage) sends GA_EV(0) to read st_str and
                    // drive the power-up sequence from wherever the FSM
                    // currently is; the R1 membership tightening NAK'd it
                    // and PluginHello rejects any ack:false, so the loop
                    // died on its first iteration. 0 changes nothing, so
                    // ack is truthful and the R1 concern (an event that
                    // silently skips a hardware handshake) does not apply.
                    //
                    // 2026-06-23 edge-trigger fix: do NOT write InputEvent
                    // here. Pre-fix, the assignment caused
                    // UpdateRuntimeAndInputEvent next scan to see
                    // PreviousInputEvent != InputEvent and re-fire the
                    // same Transition -- EV_RESET / EV_ERROR via GA_EV
                    // then triggered xForceReentry twice and SelfReentryCount
                    // lied about the count. Pass GaEvRaw direct so the
                    // edge-detect mechanism stays scoped to VisuEventControl
                    // (its original owner) and TCP events bypass it cleanly.
                    IF GaEvRaw <> 0 THEN
                        AxisGroupManagerFb.TRANSITION(
                            eEvent := TO_UDINT(GaEvRaw)
                        );
                    END_IF;

                    ResponsePacketPointer^.ele_count := 0;
                    // NOTE: PrevAxisGroupState is intentionally NOT updated here.
                    // The ST_CHG push-event detector above at line ~255 owns that
                    // variable; pre-updating it would suppress the ST_CHG event
                    // for any transition triggered via SYS/GA_EV (verified live
                    // 2026-04-24: UnInited->Powering was silently swallowed).
                    StateString := TO_STRING(AxisGroupManagerFb._eState);
                    MessagePackerFb.PackKvDint(ADR('st'), AxisGroupManagerFb._eState);
                    MessagePackerFb.PackKvStr(ADR('st_str'), ADR(StateString));
                    // Error latch: empty string + 0 when no error has been recorded
                    // since the last EV_RESET -> UnInited cycle.
                    MessagePackerFb.PackKvStr(ADR('err_src'), ADR(GVL.LastErrorSource));
                    MessagePackerFb.PackKvDint(ADR('err_id'), TO_DINT(GVL.LastErrorID));
                END_IF
            ELSIF (CommandType = 'PING') THEN
                // A3: UI heartbeat. Stamp last-seen and bump counter; reply with runtime.
                // Track worst-case PING-to-PING gap (PLC clock) for comm-jitter
                // visibility. Skip first sample after reset (PrevPingRuntimeMs=0).
                IF (GVL.PrevPingRuntimeMs > 0) THEN
                    PingGapMs := TO_UDINT(RuntimeMs - GVL.PrevPingRuntimeMs);
                    IF (PingGapMs > GVL.PingMaxGapMs) THEN
                        GVL.PingMaxGapMs := PingGapMs;
                    END_IF;
                END_IF;
                GVL.PrevPingRuntimeMs := RuntimeMs;
                GVL.LastUiPingMs := RuntimeMs;
                GVL.UiPingCount  := GVL.UiPingCount + 1;
                ShouldReturn := TRUE;
                CommandAck := TRUE;
                ResponsePacketPointer^.ele_count := 0;
                MessagePackerFb.PackKvBool(ADR('pong'), TRUE);
                MessagePackerFb.PackKvLint(ADR('runtime_ms'), RuntimeMs);
            ELSIF (CommandType = 'GET_MACHINE_STATE') THEN
                // A4: Full-snapshot read for UI reconnect. Doesn't change FSM.
                ShouldReturn := TRUE;
                CommandAck := TRUE;
                StateString := TO_STRING(AxisGroupManagerFb._eState);
                // Per-axis fault snapshot (W5 PLC half). DS402 drives on this
                // bus don't expose nErrorID/LastError (verified live), so we
                // surface bError as a 4-bit mask + nAxisState per axis. UI can
                // tell the operator "Axis 2 errorstop" instead of "Error".
                AxesErrMask := 0;
                IF IoConfig_Globals.EAxis0.bError THEN AxesErrMask := AxesErrMask OR 16#1; END_IF;
                IF IoConfig_Globals.EAxis1.bError THEN AxesErrMask := AxesErrMask OR 16#2; END_IF;
                IF IoConfig_Globals.EAxis2.bError THEN AxesErrMask := AxesErrMask OR 16#4; END_IF;
                IF reelpullmotor.bError           THEN AxesErrMask := AxesErrMask OR 16#8; END_IF;
                // 16 top-level keys: 12 baseline + reel_pos (§4 (1)) +
                // scratchpad + boot_epoch_now (§4 (2)) +
                // last_completed_movement_id (§4 (2) intent reconcile,
                // per implementation_review_2026-06-22.md §2).
                ResponsePacketPointer^.ele_count := 0;
                MessagePackerFb.PackKvDint(ADR('st'), AxisGroupManagerFb._eState);
                MessagePackerFb.PackKvStr(ADR('st_str'), ADR(StateString));
                MessagePackerFb.PackKvStr(ADR('err_src'), ADR(GVL.LastErrorSource));
                MessagePackerFb.PackKvDint(ADR('err_id'), TO_DINT(GVL.LastErrorID));
                MessagePackerFb.PackKvLint(ADR('motion_buffer_size'), TO_LINT(MotionBufferSize));
                MessagePackerFb.PackKvLint(ADR('movement_id'), TO_LINT(GroupReadPositionFb.MovementId));
                MessagePackerFb.PackKvLint(ADR('runtime_ms'), RuntimeMs);
                MessagePackerFb.PackKvBool(ADR('coord_set'), GVL.CoordSystemConfigured);
                MessagePackerFb.PackKvDint(ADR('axes_err_mask'), TO_DINT(AxesErrMask));
                // 'axes_state' raw pack (multi-line OR-expression awkward
                // for PackKvDint signature). +1 manually.
                ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 1;
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('axes_state'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue :=
                    TO_DINT(IoConfig_Globals.EAxis0.nAxisState)
                    OR (TO_DINT(IoConfig_Globals.EAxis1.nAxisState) * 256)
                    OR (TO_DINT(IoConfig_Globals.EAxis2.nAxisState) * 65536)
                    OR (TO_DINT(reelpullmotor.nAxisState) * 16777216));
                // axes_err_id : msgpack array of 4 ints (DS402
                // uiDriveInterfaceError per axis, latched cyclically into
                // GVL.LastAxesErrorIDs). Same axis ordering as axes_err_mask
                // bits 0..3. UINT zero-extends cleanly into DINT for wire.
                MessagePackerFb.PackKvArray(ADR('axes_err_id'), 4);
                MessagePackerFb.PackElemDint(UINT_TO_DINT(GVL.LastAxesErrorIDs[0]));
                MessagePackerFb.PackElemDint(UINT_TO_DINT(GVL.LastAxesErrorIDs[1]));
                MessagePackerFb.PackElemDint(UINT_TO_DINT(GVL.LastAxesErrorIDs[2]));
                MessagePackerFb.PackElemDint(UINT_TO_DINT(GVL.LastAxesErrorIDs[3]));
                // axes_labels : self-describing axis order, same indexing as
                // axes_err_id / axes_err_mask bits 0..3. UI consumes this
                // instead of hardcoding labels, collapsing the four-site
                // axes_err_id ordering coupling (PLC pack / GVL / mask bits /
                // UI labels) down to a single source of truth on the PLC.
                MessagePackerFb.PackKvArray(ADR('axes_labels'), 4);
                MessagePackerFb.PackElemStr(ADR('EAxis0 (arm1)'));
                MessagePackerFb.PackElemStr(ADR('EAxis1 (arm2)'));
                MessagePackerFb.PackElemStr(ADR('EAxis2 (arm3)'));
                MessagePackerFb.PackElemStr(ADR('reelpullmotor'));
                // axes_sim_mask : effective per-axis simulation mask, same
                // bit order as axes_labels (bit0..2 delta, bit3 reel;
                // 1 = simulated). Applied-state, not request -- a host
                // that waits for its SET_AXIS_SIM mask to show up here
                // before EV_POWER_ON never powers half-switched drives.
                MessagePackerFb.PackKvDint(ADR('axes_sim_mask'), TO_DINT(GVL.AxisSimMask));
                // reel_pos : reelpullmotor.fActPosition (REAL, axis-scaled
                // user units -- mm under current GenericDSP402 scaling).
                // Driven by decisions_2026-06-22.md §4 (1): renderer uses
                // this on resume to derive the carrier-tape cell number
                // (cell = round((reel_pos - origin) / pitch)). PLC stays
                // generic and reports raw position; the "origin" and
                // "pitch" semantics live entirely on the host.
                // Retention across PLC restart scenarios verified by
                // probe_reel_retain (STOP+START path conclusive).
                MessagePackerFb.PackKvReal(ADR('reel_pos'), reelpullmotor.fActPosition);
                // scratchpad : nested map carrying the host-owned resume
                // cursor. PLC pipes the retained Scratchpad_v1 struct out
                // verbatim; renderer compares boot_epoch vs GVL.BootEpochCount
                // (renderer also reads BootEpochCount separately?) to detect
                // staleness. See decisions_2026-06-22.md §4 (2) + B.6.
                // 7 nested keys.
                MessagePackerFb.PackKvMap(ADR('scratchpad'), 7);
                MessagePackerFb.PackPairDint(ADR('schema_version'),     TO_DINT(GVL.ScratchpadV1.SchemaVersion));
                MessagePackerFb.PackPairDint(ADR('plan_id'),            UDINT_TO_DINT(GVL.ScratchpadV1.PlanId));
                MessagePackerFb.PackPairDint(ADR('plan_index'),         UDINT_TO_DINT(GVL.ScratchpadV1.PlanIndex));
                MessagePackerFb.PackPairDint(ADR('intent_kind'),        TO_DINT(GVL.ScratchpadV1.IntentKind));
                MessagePackerFb.PackPairDint(ADR('intent_movement_id'), UDINT_TO_DINT(GVL.ScratchpadV1.IntentMovementId));
                MessagePackerFb.PackPairDint(ADR('last_vision_pulse'),  UDINT_TO_DINT(GVL.ScratchpadV1.LastVisionPulse));
                MessagePackerFb.PackPairDint(ADR('boot_epoch'),         UDINT_TO_DINT(GVL.ScratchpadV1.BootEpoch));
                // boot_epoch_now : current GVL.BootEpochCount, top-level peer
                // of scratchpad. Renderer compares scratchpad.boot_epoch
                // (captured at last write) against this; mismatch -> PLC
                // restarted since the write -> scratchpad stale.
                MessagePackerFb.PackKvDint(ADR('boot_epoch_now'), UDINT_TO_DINT(GVL.BootEpochCount));
                // last_completed_movement_id : latched at the MOVE_DONE
                // emit site (UpdateMotionProgress.st line 81 / 92), so it
                // tracks "the last move that actually finished and drained
                // the buffer", NOT "the move id corresponding to current
                // arm position" (which is what `movement_id` above is,
                // updated as soon as a move starts executing). Renderer
                // resume reconcile MUST compare its scratchpad
                // intent_movement_id against THIS field, not `movement_id`
                // -- otherwise a move that started but didn't finish is
                // mis-reported as completed. See
                // implementation_review_2026-06-22.md §2.
                MessagePackerFb.PackKvLint(ADR('last_completed_movement_id'), TO_LINT(PrevCompletedMovementId));
            ELSIF (CommandType = 'SCRATCHPAD_WRITE') THEN
                // §4 (2) -- host write path. Take the host-controlled fields
                // off the request and store them. PLC NEVER interprets any of
                // these except SchemaVersion (always set to 1 here so a stored
                // scratchpad is distinguishable from a cold-reset-wiped one)
                // and BootEpoch (captured at write time for staleness
                // detection on the next read).
                //
                // Wire contract for v1: caller sends ALL five host-controlled
                // fields every write. Partial updates require FindValueByPath
                // (currently private to the unpacker) and were judged
                // unnecessary for v1 -- the host writes the entire cursor
                // before each unrecoverable action anyway, so partial-write
                // optimisation buys nothing.
                //
                // 2026-06-23 code-review fix: detect missing fields with a
                // -1 sentinel default and NAK the whole write rather than
                // silently zero-stomp the slot. Pre-fix, a host bug that
                // omitted e.g. 'intent_movement_id' set it to 0, ACKed, and
                // broke the next resume reconcile (host thinks no move was
                // in flight when in fact the previous intent is now lost).
                ShouldReturn := TRUE;
                ScratchpadPlanIdRaw          := MessageUnpackerFb.TryReadINT64('plan_id', -1);
                ScratchpadPlanIndexRaw       := MessageUnpackerFb.TryReadINT64('plan_index', -1);
                ScratchpadIntentKindRaw      := MessageUnpackerFb.TryReadINT64('intent_kind', -1);
                ScratchpadIntentMidRaw       := MessageUnpackerFb.TryReadINT64('intent_movement_id', -1);
                ScratchpadLastVisionPulseRaw := MessageUnpackerFb.TryReadINT64('last_vision_pulse', -1);
                IF (ScratchpadPlanIdRaw < 0)
                   OR (ScratchpadPlanIndexRaw < 0)
                   OR (ScratchpadIntentKindRaw < 0)
                   OR (ScratchpadIntentMidRaw < 0)
                   OR (ScratchpadLastVisionPulseRaw < 0) THEN
                    // Partial write -> refuse to commit; existing scratchpad
                    // contents survive untouched so the host can retry.
                    CommandAck := FALSE;
                    GVL.ScratchpadPartialNakCount := GVL.ScratchpadPartialNakCount + 1;
                    ResponsePacketPointer^.ele_count := 0;
                    // R9 fix: 'ack' packed once by CommitReplySlotOrScratch.
                    MessagePackerFb.PackKvStr(ADR('err'), ADR('partial_scratchpad'));
                ELSIF (ScratchpadPlanIdRaw > 2147483647)
                      OR (ScratchpadPlanIndexRaw > 2147483647)
                      OR (ScratchpadIntentKindRaw > 255)
                      OR (ScratchpadIntentMidRaw > 2147483647)
                      OR (ScratchpadLastVisionPulseRaw > 2147483647) THEN
                    // R7 fix: values beyond the storage/readback width used
                    // to TO_UDINT-wrap silently AND ack -- the host read back
                    // a different number than it wrote (defeats the
                    // partial-write NAK's spirit). Slot types are UDINT/USINT
                    // but GET_MACHINE_STATE reads back via UDINT_TO_DINT, so
                    // the round-trippable range is 0..2^31-1 (0..255 for
                    // intent_kind). Out-of-range -> NAK, existing contents
                    // survive untouched.
                    CommandAck := FALSE;
                    GVL.ScratchpadPartialNakCount := GVL.ScratchpadPartialNakCount + 1;
                    ResponsePacketPointer^.ele_count := 0;
                    MessagePackerFb.PackKvStr(ADR('err'), ADR('scratchpad_range'));
                ELSE
                    CommandAck := TRUE;
                    GVL.ScratchpadV1.PlanId           := TO_UDINT(ScratchpadPlanIdRaw);
                    GVL.ScratchpadV1.PlanIndex        := TO_UDINT(ScratchpadPlanIndexRaw);
                    GVL.ScratchpadV1.IntentKind       := TO_USINT(ScratchpadIntentKindRaw);
                    GVL.ScratchpadV1.IntentMovementId := TO_UDINT(ScratchpadIntentMidRaw);
                    GVL.ScratchpadV1.LastVisionPulse  := TO_UDINT(ScratchpadLastVisionPulseRaw);
                    GVL.ScratchpadV1.SchemaVersion := 1;
                    GVL.ScratchpadV1.BootEpoch := GVL.BootEpochCount;
                    ResponsePacketPointer^.ele_count := 0;
                    // R9 fix: 'ack' packed once by CommitReplySlotOrScratch.
                END_IF
            ELSIF (CommandType = 'RESET_DBG_INFO') THEN
                // SYS-side reset: clears comm-stability counters without
                // requiring FSM=Ready. R8 fix: the reset list lives in ONE
                // place -- ResetDiagCounters() -- shared with the legacy
                // M-path branch. The two inline lists had drifted (each
                // missed counters the other cleared, and both missed the
                // newer ones).
                ResetDiagCounters();
                ShouldReturn := TRUE;
                CommandAck := TRUE;
                ResponsePacketPointer^.ele_count := 0;
                MessagePackerFb.PackKvBool(ADR('reset'), TRUE);
            ELSIF (CommandType = 'SET_OVERRIDE') THEN
                // Speed override for slow-motion observation (GVL.OverrideFactor).
                // factor 0.01..1; the reply carries the value now in force.
                ShouldReturn := TRUE;
                CommandAck := TRUE;
                ResponsePacketPointer^.ele_count := 0;
                GVL.OverrideFactor := LIMIT(0.01, TO_LREAL(MessageUnpackerFb.TryReadREAL('factor', TO_REAL(GVL.OverrideFactor))), 1.0);
                MessagePackerFb.PackKvReal(ADR('factor'), TO_REAL(GVL.OverrideFactor));
            ELSIF (CommandType = 'DI_WATCH') THEN
                // Register digital inputs for change events (UpdateDiWatch):
                // mask = pins, edge = 1 rising / 2 falling / 3 both / 0 stop
                // watching them, throttle_ms = at most one event per pin per
                // that long (changes in between are summarised). Cleared
                // when the host disconnects. Replies with the pins watched.
                ShouldReturn := TRUE;
                CommandAck := TRUE;
                ResponsePacketPointer^.ele_count := 0;
                DiMaskIn := LINT_TO_WORD(MessageUnpackerFb.TryReadINT64('mask', 0));
                DiEdgeIn := MessageUnpackerFb.TryReadINT64('edge', 3);
                DiThrIn := LIMIT(0, MessageUnpackerFb.TryReadINT64('throttle_ms', 200), 60000);
                FOR DiIdx := 0 TO 15 DO
                    IF (SHR(DiMaskIn, DiIdx) AND 1) <> 0 THEN
                        IF (DiEdgeIn AND 3) = 0 THEN
                            DiWatchMask := DiWatchMask AND NOT SHL(WORD#1, DiIdx);
                        ELSE
                            DiWatchMask := DiWatchMask OR SHL(WORD#1, DiIdx);
                        END_IF;
                        IF (DiEdgeIn AND 1) <> 0 THEN
                            DiWatchRise := DiWatchRise OR SHL(WORD#1, DiIdx);
                        ELSE
                            DiWatchRise := DiWatchRise AND NOT SHL(WORD#1, DiIdx);
                        END_IF;
                        IF (DiEdgeIn AND 2) <> 0 THEN
                            DiWatchFall := DiWatchFall OR SHL(WORD#1, DiIdx);
                        ELSE
                            DiWatchFall := DiWatchFall AND NOT SHL(WORD#1, DiIdx);
                        END_IF;
                        DiWatchThrottleMs[DiIdx] := DiThrIn;
                        DiWFlips[DiIdx] := 0;
                        DiWHighMs[DiIdx] := 0;
                        DiWMaxHighMs[DiIdx] := 0;
                        DiWMaxLowMs[DiIdx] := 0;
                        DiWRunMs[DiIdx] := 0;
                        DiWPending[DiIdx] := FALSE;
                        DiWLastSentMs[DiIdx] := RuntimeMs - DiThrIn;   // the first change goes out at once
                    END_IF;
                END_FOR;
                MessagePackerFb.PackKvDint(ADR('mask'), WORD_TO_DINT(DiWatchMask));
                MessagePackerFb.PackKvLint(ADR('state'), ULINT_TO_LINT(DigitalInputBits_Curr));
            ELSIF (CommandType = 'REEL_RESUME') THEN
                // Finish a tape move a fault stopped short (GVL.ReelMv*):
                // move the rest of the way with F/ACC/DEA/JERK, counting
                // the cells as they pass. Acks {rest_mm} at dispatch; the
                // host polls PLAN_GET until reel_open goes FALSE.
                ShouldReturn := TRUE;
                ResponsePacketPointer^.ele_count := 0;
                ReelRestMm := DINT_TO_LREAL(GVL.ReelMvCells) * GVL.ReelMvCellMm - GVL.ReelMvTravelMm;
                CommandAck := FALSE;
                // Has the reel moved since the tracker closed (bus restart,
                // the drive's own fault reaction, a manual ReelGo)? Then the
                // recorded travel no longer says where the tape is.
                ReelPosDelta := reelpullmotor.fActPosition - GVL.ReelMvStopPos;
                IF reelpullmotor.fPositionPeriod > 0 THEN
                    WHILE ReelPosDelta > reelpullmotor.fPositionPeriod / 2 DO
                        ReelPosDelta := ReelPosDelta - reelpullmotor.fPositionPeriod;
                    END_WHILE;
                    WHILE ReelPosDelta < -reelpullmotor.fPositionPeriod / 2 DO
                        ReelPosDelta := ReelPosDelta + reelpullmotor.fPositionPeriod;
                    END_WHILE;
                END_IF;
                IF GVL.ReelMvOpen AND NOT ReelTrkActive AND (ABS(ReelPosDelta) > REEL_POS_TOL_MM) THEN
                    GVL.ReelMvPosLost := TRUE;
                END_IF;
                IF NOT GVL.ReelMvOpen THEN
                    CommandAck := TRUE;
                    ReelRestMm := 0;
                ELSIF GVL.ReelMvPosLost THEN
                    MessagePackerFb.PackKvStr(ADR('err'), ADR('reel_position_lost'));
                ELSIF TapeActive OR ReelTrkActive THEN
                    MessagePackerFb.PackKvStr(ADR('err'), ADR('tape_busy'));
                ELSIF NOT AxisGroupReady THEN
                    MessagePackerFb.PackKvStr(ADR('err'), ADR('group_not_ready'));
                ELSIF reelMoveRelative.Busy AND reelMoveRelative2.Busy THEN
                    MessagePackerFb.PackKvStr(ADR('err'), ADR('reel_busy'));
                ELSIF ReelRestMm <= REEL_CELL_EPS_MM THEN
                    // at the target already: count what is left and close
                    ReelClearN := GVL.ReelMvCells - GVL.ReelMvCounted;
                    PlanCountCells(n := ReelClearN, kind := GVL.ReelMvKind);
                    GVL.ReelMvCounted := GVL.ReelMvCells;
                    GVL.ReelMvOpen := FALSE;
                    CommandAck := TRUE;
                    ReelRestMm := 0;
                ELSE
                    reelGoDistance := LREAL_TO_REAL(ReelRestMm);
                    reelGoVelocity := MessageUnpackerFb.TryReadREAL('F',    reelGoVelocity);
                    reelGoAccel    := MessageUnpackerFb.TryReadREAL('ACC',  reelGoAccel);
                    reelGoDecel    := MessageUnpackerFb.TryReadREAL('DEA',  reelGoDecel);
                    reelGoJerk     := MessageUnpackerFb.TryReadREAL('JERK', reelGoJerk);
                    IF NOT reelMoveRelative.Busy THEN
                        reelGoRequest := TRUE;
                    ELSE
                        reelGoRequest2 := TRUE;
                    END_IF;
                    ReelTrkActive := TRUE;
                    ReelTrkStarted := FALSE;
                    ReelTrkStill := 0;
                    ReelTrkAnchor := GVL.ReelMvTravelMm;
                    ReelTrkT0 := RuntimeMs;
                    ReelTrkLastPos := reelpullmotor.fActPosition;
                    CommandAck := TRUE;
                END_IF;
                MessagePackerFb.PackKvReal(ADR('rest_mm'), LREAL_TO_REAL(ReelRestMm));
                MessagePackerFb.PackKvReal(ADR('moved_mm'), LREAL_TO_REAL(ReelPosDelta));
            ELSIF (CommandType = 'REEL_CLEAR') THEN
                // Close an interrupted tape move without moving (the
                // operator aligned the tape by hand): count `count` more of
                // its cells (0..the uncounted rest) into the plan.
                ShouldReturn := TRUE;
                ResponsePacketPointer^.ele_count := 0;
                IF ReelTrkActive THEN
                    CommandAck := FALSE;
                    MessagePackerFb.PackKvStr(ADR('err'), ADR('tape_busy'));
                ELSE
                    IF GVL.ReelMvOpen THEN
                        ReelClearN := LIMIT(0, TO_DINT(MessageUnpackerFb.TryReadINT64('count', 0)),
                                            GVL.ReelMvCells - GVL.ReelMvCounted);
                        PlanCountCells(n := ReelClearN, kind := GVL.ReelMvKind);
                        GVL.ReelMvCounted := GVL.ReelMvCounted + ReelClearN;
                    END_IF;
                    GVL.ReelMvOpen := FALSE;
                    GVL.ReelMvPosLost := FALSE;
                    CommandAck := TRUE;
                    MessagePackerFb.PackKvLint(ADR('cells_done'), UDINT_TO_LINT(GVL.PlanCellsDone));
                END_IF;
            ELSIF (CommandType = 'PLAN_SET') THEN
                // Store the production plan in retained memory (GVL.PlanSeg).
                // seg: array of 1..32 non-zero DINTs; plan_id: host's label.
                // Validated into PlanNew first so a bad plan never replaces a
                // good one. Ordered SYS: it stays FIFO with TAPE_CYCLE.
                ShouldReturn := TRUE;
                ResponsePacketPointer^.ele_count := 0;
                PlanOkIn := FALSE;
                PlanN := 0;
                IF MessageUnpackerFb.TryReadObject('seg', ADR(fbPinOpSeqUnpacker)) THEN
                    fbPinOpSeqUnpacker.UnpackNext();
                    PlanN := TO_INT(fbPinOpSeqUnpacker.uiLastCount);
                    PlanOkIn := (PlanN >= 1) AND (PlanN <= 32);
                    IF PlanOkIn THEN
                        FOR PlanIdx := 0 TO PlanN - 1 DO
                            fbPinOpSeqUnpacker.UnpackNext();
                            PlanNew[PlanIdx] := fbPinOpSeqUnpacker.diLastDINT;
                            IF PlanNew[PlanIdx] = 0 THEN
                                PlanOkIn := FALSE;
                            END_IF;
                        END_FOR;
                    END_IF;
                END_IF;
                IF PlanOkIn THEN
                    FOR PlanIdx := 0 TO 31 DO
                        IF PlanIdx < PlanN THEN
                            GVL.PlanSeg[PlanIdx] := PlanNew[PlanIdx];
                        ELSE
                            GVL.PlanSeg[PlanIdx] := 0;
                        END_IF;
                    END_FOR;
                    GVL.PlanSegCount := PlanN;
                    GVL.PlanId := TO_DINT(MessageUnpackerFb.TryReadINT64('plan_id', 0));
                    // cells_done lets the host re-set a plan part-way through.
                    PlanCellsDoneIn := MessageUnpackerFb.TryReadINT64('cells_done', 0);
                    IF PlanCellsDoneIn < 0 THEN
                        PlanCellsDoneIn := 0;
                    END_IF;
                    GVL.PlanCellsDone := LINT_TO_UDINT(PlanCellsDoneIn);
                    // a new plan (or a re-set count) supersedes an
                    // interrupted move: the host has decided where the tape is
                    IF NOT ReelTrkActive THEN
                        GVL.ReelMvOpen := FALSE;
                        GVL.ReelMvPosLost := FALSE;
                    END_IF;
                    GVL.PlanCellsPacked := 0;
                    GVL.PlanCellsEmpty := 0;
                    GVL.PlanMismatchCount := 0;
                    GVL.PlanRev := GVL.PlanRev + 1;
                    CommandAck := TRUE;
                    MessagePackerFb.PackKvLint(ADR('plan_rev'), UDINT_TO_LINT(GVL.PlanRev));
                ELSE
                    CommandAck := FALSE;
                    MessagePackerFb.PackKvStr(ADR('err'), ADR('bad_plan'));
                END_IF;
            ELSIF (CommandType = 'PLAN_GET') THEN
                // The whole plan plus the cells advanced since PLAN_SET; the
                // renderer derives the remaining plan from the two.
                ShouldReturn := TRUE;
                CommandAck := TRUE;
                ResponsePacketPointer^.ele_count := 0;
                MessagePackerFb.PackKvDint(ADR('plan_id'), GVL.PlanId);
                MessagePackerFb.PackKvLint(ADR('plan_rev'), UDINT_TO_LINT(GVL.PlanRev));
                MessagePackerFb.PackKvLint(ADR('cells_done'), UDINT_TO_LINT(GVL.PlanCellsDone));
                MessagePackerFb.PackKvLint(ADR('cells_packed'), UDINT_TO_LINT(GVL.PlanCellsPacked));
                MessagePackerFb.PackKvLint(ADR('cells_empty'), UDINT_TO_LINT(GVL.PlanCellsEmpty));
                MessagePackerFb.PackKvLint(ADR('mismatch'), UDINT_TO_LINT(GVL.PlanMismatchCount));
                // an open tape move (GVL.ReelMv*): moving, or interrupted
                MessagePackerFb.PackKvBool(ADR('reel_open'), GVL.ReelMvOpen);
                MessagePackerFb.PackKvBool(ADR('reel_moving'), ReelTrkActive);
                MessagePackerFb.PackKvBool(ADR('reel_pos_lost'), GVL.ReelMvPosLost);
                MessagePackerFb.PackKvReal(ADR('reel_rest_mm'),
                    LREAL_TO_REAL(DINT_TO_LREAL(GVL.ReelMvCells) * GVL.ReelMvCellMm - GVL.ReelMvTravelMm));
                MessagePackerFb.PackKvDint(ADR('reel_cells'), GVL.ReelMvCells);
                MessagePackerFb.PackKvDint(ADR('reel_counted'), GVL.ReelMvCounted);
                MessagePackerFb.PackKvLint(ADR('reel_interrupts'), UDINT_TO_LINT(GVL.ReelInterruptCount));
                IF (GVL.PlanSegCount > 0) AND (GVL.PlanSegCount <= 32) THEN
                    MessagePackerFb.PackKvArray(ADR('seg'), TO_UINT(GVL.PlanSegCount));
                    FOR PlanIdx := 0 TO GVL.PlanSegCount - 1 DO
                        MessagePackerFb.PackElemDint(GVL.PlanSeg[PlanIdx]);
                    END_FOR;
                ELSE
                    MessagePackerFb.PackKvArray(ADR('seg'), 0);
                END_IF;
            ELSIF (CommandType = 'VERSION') THEN
                // Build provenance: lets an external tool verify the running
                // PLC matches a repo commit without IDE assistance. Stamped by
                // codesys_scripts/jobs/stamp_build_info.py before import_all.
                ShouldReturn := TRUE;
                CommandAck := TRUE;
                ResponsePacketPointer^.ele_count := 0;
                MessagePackerFb.PackKvStr(ADR('git_sha'), ADR(GVL.BUILD_GIT_SHA));
                MessagePackerFb.PackKvLint(ADR('build_ts_ms'), GVL.BUILD_TS_MS);
                MessagePackerFb.PackKvLint(ADR('runtime_ms'), RuntimeMs);
            ELSIF (CommandType = 'GET_DIAG') THEN
                // Generic comm/robustness counters dump. UI dashboard polls this
                // (e.g. once per second) to chart drop/NAK/reset rates and the
                // PING gap-jitter envelope. Read-only; doesn't perturb FSM.
                // Counters resettable via SYS/RESET_DBG_INFO (see existing handler).
                ShouldReturn := TRUE;
                CommandAck := TRUE;
                // GET_DIAG fields extended 2026-06-17 to be a TCP-side
                // superset of the daemon-side counter dump the fuzz tests
                // used to call via oapp.read_value. Lets fuzz/soak tests run
                // without the CODESYS RPC daemon being alive -- daemon dies
                // mid-test no longer breaks the test. New fields:
                //   self_reentry, pending_stchg_drop, pending_movedone_drop,
                //   remp_drop_reply, remp_drop_trig, dupe_cmd,
                //   io_cmd_count, io_trig_count, flyevent_avail
                ResponsePacketPointer^.ele_count := 0;
                MessagePackerFb.PackKvDint(ADR('remp_overflow_drop'), TO_DINT(GVL.ReMpOverflowDropCount));
                MessagePackerFb.PackKvLint(ADR('runtime_ms'), RuntimeMs);
                MessagePackerFb.PackKvDint(ADR('sm_scans'), TO_DINT(GVL.AxisGroupSMScans));
                MessagePackerFb.PackKvDint(ADR('remp_drop'), TO_DINT(GVL.ReMpDropCount));
                MessagePackerFb.PackKvDint(ADR('overlen_drop'), TO_DINT(GVL.OverlenDropCount));
                MessagePackerFb.PackKvDint(ADR('send_stall_drop'), TO_DINT(GVL.SendStallDropCount));
                MessagePackerFb.PackKvDint(ADR('group_not_ready_nak'), TO_DINT(GVL.GroupNotReadyNakCount));
                MessagePackerFb.PackKvDint(ADR('missing_type_nak'), TO_DINT(GVL.MissingTypeFieldNakCount));
                MessagePackerFb.PackKvDint(ADR('coord_not_cfg_nak'), TO_DINT(GVL.CoordNotConfiguredNakCount));
                MessagePackerFb.PackKvDint(ADR('proto_mismatch_nak'), TO_DINT(GVL.ProtocolVersionMismatchCount));
                MessagePackerFb.PackKvDint(ADR('idle_reset'), TO_DINT(GVL.IdleResetCount));
                MessagePackerFb.PackKvDint(ADR('read_err_reset'), TO_DINT(GVL.ReadErrorResetCount));
                MessagePackerFb.PackKvDint(ADR('parser_err_reset'), TO_DINT(GVL.ParserErrorResetCount));
                MessagePackerFb.PackKvDint(ADR('write_err_reset'), TO_DINT(GVL.WriteErrorResetCount));
                MessagePackerFb.PackKvDint(ADR('client_connect_count'), TO_DINT(GVL.ClientConnectCount));
                MessagePackerFb.PackKvDint(ADR('server_long_idle_count'), TO_DINT(GVL.ServerLongIdleCount));
                MessagePackerFb.PackKvBool(ADR('server_active'), TCP_MSGPAK_Server.fbMyServer.xServerActive);
                MessagePackerFb.PackKvStr(ADR('bind_addr'), ADR(TCP_MSGPAK_Server.ipServerAddress.ipAddress));
                MessagePackerFb.PackKvDint(ADR('ui_ping_count'), TO_DINT(GVL.UiPingCount));
                MessagePackerFb.PackKvDint(ADR('ui_hb_stale_count'), TO_DINT(GVL.UiHeartbeatStaleCount));
                MessagePackerFb.PackKvDint(ADR('group_error_stop_trips'), TO_DINT(GVL.GroupErrorStopTripCount));
                MessagePackerFb.PackKvDint(ADR('ping_max_gap_ms'), TO_DINT(GVL.PingMaxGapMs));
                MessagePackerFb.PackKvLint(ADR('last_ui_ping_ms'), GVL.LastUiPingMs);
                MessagePackerFb.PackKvDint(ADR('st_chg_event_count'), TO_DINT(GVL.StateChangeEventCount));
                MessagePackerFb.PackKvDint(ADR('self_reentry'), TO_DINT(GVL.SelfReentryCount));
                MessagePackerFb.PackKvDint(ADR('pending_stchg_drop'), TO_DINT(GVL.PendingStChgDropCount));
                MessagePackerFb.PackKvDint(ADR('pending_movedone_drop'), TO_DINT(GVL.PendingMoveDoneDropCount));
                MessagePackerFb.PackKvDint(ADR('remp_drop_reply'), TO_DINT(GVL.ReMpDropCount_Reply));
                MessagePackerFb.PackKvDint(ADR('remp_drop_trig'), TO_DINT(GVL.ReMpDropCount_TriggerEvent));
                MessagePackerFb.PackKvDint(ADR('dupe_cmd'), TO_DINT(GVL.DupeCommandCount));
                // R2/R3/R4/R5/R11: new NAK counters. Published here => they
                // are part of the canonical ResetDiagCounters list (R8).
                MessagePackerFb.PackKvDint(ADR('unknown_cmd_nak'), TO_DINT(GVL.UnknownCmdNakCount));
                MessagePackerFb.PackKvDint(ADR('flyevent_reject_nak'), TO_DINT(GVL.FlyEventRejectNakCount));
                MessagePackerFb.PackKvDint(ADR('flyevent_full_nak'), TO_DINT(GVL.FlyEventBufferFullNakCount));
                MessagePackerFb.PackKvDint(ADR('io_cmd_count'), TO_DINT(IoCommandCount));
                MessagePackerFb.PackKvDint(ADR('io_trig_count'), TO_DINT(IoTriggerCount));
                MessagePackerFb.PackKvDint(ADR('flyevent_avail'), TO_DINT(FlyEventAvailableCount));
            ELSIF (CommandType = 'SET_AXIS_SIM') THEN
                // Per-axis simulation mask (bench mode). bit0..2 =
                // EAxis0/1/2 (delta trio), bit3 = reelpullmotor; 1 =
                // simulated. Honored ONLY in UnInited: flipping a drive
                // between real and simulated re-runs its start-up phase
                // (SMC3_ReinitDrive), which is only safe while the group
                // is disabled and powerless. Reply echoes the EFFECTIVE
                // mask this request produces (request OR legacy 16#0F
                // gate); the applied state lands in axes_sim_mask when
                // the reinit sequence completes -- hosts must poll for
                // it before EV_POWER_ON.
                ShouldReturn := TRUE;
                AxisSimMaskRaw := MessageUnpackerFb.TryReadINT64('mask', -1);
                IF (AxisSimMaskRaw < 0) OR (AxisSimMaskRaw > 15) THEN
                    CommandAck := FALSE;
                    ResponsePacketPointer^.ele_count := 0;
                    MessagePackerFb.PackKvStr(ADR('err'), ADR('bad_mask'));
                ELSIF (AxisGroupManagerFb._eState <> E_RobotState.UnInited) THEN
                    CommandAck := FALSE;
                    ResponsePacketPointer^.ele_count := 0;
                    MessagePackerFb.PackKvStr(ADR('err'), ADR('not_uninited'));
                ELSIF (AxisSimApplySeq <> 0) THEN
                    // A previous apply is still reinitializing drives;
                    // refusing beats queueing (the host's poll loop
                    // retries trivially).
                    CommandAck := FALSE;
                    ResponsePacketPointer^.ele_count := 0;
                    MessagePackerFb.PackKvStr(ADR('err'), ADR('sim_apply_busy'));
                ELSE
                    CommandAck := TRUE;
                    GVL.AxisSimMask_Request  := TO_BYTE(AxisSimMaskRaw);
                    GVL.AxisSimMaskSetMs     := RuntimeMs;
                    GVL.AxisSimHostRequested := TRUE;
                    ResponsePacketPointer^.ele_count := 0;
                    MessagePackerFb.PackKvDint(ADR('mask'),
                        TO_DINT(GVL.AxisSimMask_Request OR GVL.AxisSimConfigMask
                                OR SEL(GVL.bVirtualMotorsMode, 16#00, 16#0F)));
                END_IF
            ELSIF (CommandType = 'COORD1_BIND') THEN
                // Bind the coord1 tracking frame to the conveyor encoder.
                // Phase 4 step 2: PCS_1 is now driven by MC_TrackConveyorBelt
                // -- the kernel keeps the PCS origin in sync with the belt
                // continuously while we hold Coord1Bound TRUE. Linear belt
                // along WCS +X only for this step; rotary tables + non-X
                // belts + cam tables are all rejected here and routed
                // through future bind kinds. Reject if motion buffer is
                // non-empty so the active move's coord frame can't change
                // mid-flight, and reject re-bind while already bound
                // (caller must UNBIND first so the kernel sees a clean
                // falling edge on the TCB Execute).
                // B3 fix (analysis 2.1): the shared validation + commit
                // (busy / rebind / scale-x, then the GVL + belt-origin
                // writes) moved into Coord1CommitBind -- the SAME method
                // the FlyEvent coord1_bind action calls -- so the two
                // bind paths can never drift again. Caller-side checks
                // below are the wire-shape gates the method can't see
                // (belt_kind, cam_table, non-axial scale components).
                // Staging locals (not GVL) also fix a pre-existing wart:
                // a REJECTED bind used to stomp GVL.Coord1RefXyz/Scale.
                ShouldReturn := TRUE;
                BindReject := FALSE;
                BindRejectReason := '';

                BindKind := MessageUnpackerFb.TryReadString('belt_kind', 'linear');

                IF BindKind <> 'linear' AND BindKind <> '' THEN
                    BindReject := TRUE;
                    BindRejectReason := 'belt_kind_not_yet_supported';
                END_IF

                IF NOT BindReject THEN
                    // Read top-level scalars (path-based, defaulting to 0).
                    BindRefPulseW := TO_DINT(MessageUnpackerFb.TryReadINT64('ref_pulse', 0));
                    BindRefXW     := TO_LREAL(MessageUnpackerFb.TryReadREAL('ref_xyz[0]', 0.0));
                    BindRefYW     := TO_LREAL(MessageUnpackerFb.TryReadREAL('ref_xyz[1]', 0.0));
                    BindRefZW     := TO_LREAL(MessageUnpackerFb.TryReadREAL('ref_xyz[2]', 0.0));
                    BindScaleXW   := TO_LREAL(MessageUnpackerFb.TryReadREAL('scale[0]', 0.0));
                    BindScaleYW   := TO_LREAL(MessageUnpackerFb.TryReadREAL('scale[1]', 0.0));
                    BindScaleZW   := TO_LREAL(MessageUnpackerFb.TryReadREAL('scale[2]', 0.0));

                    // Optional cam_table.master[] probe; Phase 4 step 2
                    // PCS_1 path is linear-only so we treat any cam_table
                    // as a rejected request rather than trying to parse it.
                    BindHasTable := MessageUnpackerFb.TryReadObject('cam_table.master', ADR(fbCoord1CamUnpacker));
                END_IF

                IF NOT BindReject AND BindHasTable THEN
                    BindReject := TRUE;
                    BindRejectReason := 'cam_table_not_supported_in_pcs_mode';
                    GVL.Coord1BadTableNakCount := GVL.Coord1BadTableNakCount + 1;
                END_IF

                // Phase 4 step 2 belt-direction gate: belt must move
                // along WCS +X (Scale[1] = Scale[2] = 0). Non-axial / 2D
                // belt support means a non-zero ConveyorBeltOrigin.A
                // rotation -- added when needed. Caller-side because the
                // shared method only takes the X component (mirrors the
                // fly path's own caller-side check).
                IF NOT BindReject AND (BindScaleYW <> 0.0 OR BindScaleZW <> 0.0) THEN
                    BindReject := TRUE;
                    BindRejectReason := 'belt_direction_not_axis_x';
                END_IF

                IF NOT BindReject THEN
                    // Shared validation + commit. exitPulseOffset -1 =
                    // no window check (SYS bind is the dev/test path;
                    // production binds go via FlyEvent with a budget).
                    IF Coord1CommitBind(
                        refX            := BindRefXW,
                        refY            := BindRefYW,
                        refZ            := BindRefZW,
                        scaleX          := BindScaleXW,
                        refPulse        := BindRefPulseW,
                        exitPulseOffset := -1,
                        eventId         := 0,
                        RejectReason    => BindRejectReason) THEN
                        GVL.Coord1BindCount := GVL.Coord1BindCount + 1;
                        CommandAck := TRUE;
                        ResponsePacketPointer^.ele_count := 0;
                        MessagePackerFb.PackKvStr(ADR('mode'), ADR('linear'));
                        MessagePackerFb.PackKvDint(ADR('n'), 0);
                    ELSE
                        BindReject := TRUE;
                        IF BindRejectReason = 'coord_bind_busy' THEN
                            GVL.Coord1BindBusyNakCount := GVL.Coord1BindBusyNakCount + 1;
                        END_IF
                    END_IF
                END_IF

                IF BindReject THEN
                    CommandAck := FALSE;
                    ResponsePacketPointer^.ele_count := 0;
                    MessagePackerFb.PackKvStr(ADR('err'), ADR(BindRejectReason));
                END_IF
            ELSIF (CommandType = 'COORD1_UNBIND') THEN
                // Phase 4 step 2: drop GVL.Coord1Bound so the per-scan tcb()
                // sees a falling edge and releases PCS_1. Required before
                // a subsequent COORD1_BIND since rebind under an active
                // Execute is rejected.
                ShouldReturn := TRUE;
                CommandAck := TRUE;
                GVL.Coord1Bound := FALSE;
                ResponsePacketPointer^.ele_count := 0;

            ELSIF (CommandType = 'GET_COORD1_DEBUG') THEN
                // Coord1 bind/tracking inspector. Echoes the raw conveyor
                // pulse, bind state, ref/scale, and the per-scan diagnostic
                // Coord1OriginNow (PRE-Phase-4-step-2 prediction of where
                // PCS_1's origin should be in WCS -- the real motion path
                // is the MC_TrackConveyorBelt FB; this field is for
                // sanity-check visibility only). Read-only.
                ShouldReturn := TRUE;
                CommandAck := TRUE;
                ResponsePacketPointer^.ele_count := 0;
                MessagePackerFb.PackKvBool(ADR('bound'), GVL.Coord1Bound);
                IF GVL.Coord1CamMode = GVL.COORD1_MODE_TABLE THEN
                    MessagePackerFb.PackKvStr(ADR('mode'), ADR('table'));
                ELSE
                    MessagePackerFb.PackKvStr(ADR('mode'), ADR('linear'));
                END_IF
                MessagePackerFb.PackKvDint(ADR('cam_n'), TO_DINT(GVL.Coord1CamCount));
                IF GVL.Coord1CamCount > 0 THEN
                    MessagePackerFb.PackKvDint(ADR('cam_first'), GVL.Coord1CamMaster[0]);
                ELSE
                    MessagePackerFb.PackKvDint(ADR('cam_first'), 0);
                END_IF
                IF GVL.Coord1CamCount > 0 THEN
                    MessagePackerFb.PackKvDint(ADR('cam_last'), GVL.Coord1CamMaster[GVL.Coord1CamCount - 1]);
                ELSE
                    MessagePackerFb.PackKvDint(ADR('cam_last'), 0);
                END_IF
                MessagePackerFb.PackKvDint(ADR('pulse_raw'), GVL.ConveyorPulseRaw);
                MessagePackerFb.PackKvDint(ADR('ref_pulse'), GVL.Coord1RefPulse);
                MessagePackerFb.PackKvReal(ADR('ref_x'), TO_REAL(GVL.Coord1RefXyz[0]));
                MessagePackerFb.PackKvReal(ADR('ref_y'), TO_REAL(GVL.Coord1RefXyz[1]));
                MessagePackerFb.PackKvReal(ADR('ref_z'), TO_REAL(GVL.Coord1RefXyz[2]));
                MessagePackerFb.PackKvReal(ADR('scale_x'), TO_REAL(GVL.Coord1Scale[0]));
                MessagePackerFb.PackKvReal(ADR('scale_y'), TO_REAL(GVL.Coord1Scale[1]));
                MessagePackerFb.PackKvReal(ADR('scale_z'), TO_REAL(GVL.Coord1Scale[2]));
                MessagePackerFb.PackKvReal(ADR('origin_x'), TO_REAL(GVL.Coord1OriginNow[0]));
                MessagePackerFb.PackKvReal(ADR('origin_y'), TO_REAL(GVL.Coord1OriginNow[1]));
                MessagePackerFb.PackKvReal(ADR('origin_z'), TO_REAL(GVL.Coord1OriginNow[2]));
                MessagePackerFb.PackKvDint(ADR('bind_count'), TO_DINT(GVL.Coord1BindCount));
                MessagePackerFb.PackKvDint(ADR('bind_busy_nak'), TO_DINT(GVL.Coord1BindBusyNakCount));
                MessagePackerFb.PackKvDint(ADR('bad_table_nak'), TO_DINT(GVL.Coord1BadTableNakCount));
                // Phase 4 step 2 kernel-side diagnostic. Two forms:
                //   kernel_origin_*  = static ConveyorBeltOrigin (what
                //                      MC_ReadCoordinateTransform reports;
                //                      = ref_xyz - ref_pulse * pulse_to_mm).
                //   live_kernel_origin_* = kernel static + current encoder
                //                      position * belt_direction. Belt is
                //                      pinned to +X by the bind-time gate,
                //                      so only X gets the encoder add.
                //                      This is the LIVE WCS point that
                //                      G1 frame=1 Position=(0,0,0) targets.
                // Cross-check: prediction (origin_*) should equal live_kernel.
                MessagePackerFb.PackKvReal(ADR('kernel_origin_x'), TO_REAL(Pcs1KernelTransform.X));
                MessagePackerFb.PackKvReal(ADR('kernel_origin_y'), TO_REAL(Pcs1KernelTransform.Y));
                MessagePackerFb.PackKvReal(ADR('kernel_origin_z'), TO_REAL(Pcs1KernelTransform.Z));
                MessagePackerFb.PackKvReal(ADR('live_kernel_origin_x'), TO_REAL(Pcs1KernelTransform.X + ConveyorEncoderAxis.fActPosition));
                MessagePackerFb.PackKvReal(ADR('live_kernel_origin_y'), TO_REAL(Pcs1KernelTransform.Y));
                MessagePackerFb.PackKvReal(ADR('live_kernel_origin_z'), TO_REAL(Pcs1KernelTransform.Z));
                // Live arm TCP position (WCS). Folded into GET_COORD1_DEBUG
                // so the viz tool can sample arm motion via SYS path
                // without being blocked by the motion FSM gate (which
                // NAKs all type:M packets after a virtual-axis self-halt).
                MessagePackerFb.PackKvReal(ADR('arm_x'), TO_REAL(ReadPosition.c.x));
                MessagePackerFb.PackKvReal(ADR('arm_y'), TO_REAL(ReadPosition.c.y));
                MessagePackerFb.PackKvReal(ADR('arm_z'), TO_REAL(ReadPosition.c.z));
                // Phase 4 step 2 probe: SM3 motion progress for the move
                // currently in flight. With CoordSystem := PCS_1 on a
                // belt-tracking move, this is the data point we need to
                // answer "does progress 0->1 mean tracking 0->1".
                MessagePackerFb.PackKvLint(ADR('mv_id'), TO_LINT(CurrentMovementId));
                MessagePackerFb.PackKvReal(ADR('mv_progress'), CurrentMovementIdProgress);
            ELSIF (CommandType = 'EVT_MARK') THEN
                // Host timestamp mark in the event log (GVL.EvHead): the
                // renderer tags what only it sees (vision results in,
                // feeder commands out) on the PLC's 1 ms time base. Sent
                // fire-and-forget (no id): replies only when an id is given.
                EvSlot := GVL.EvHead MOD 1024;
                GVL.EvT[EvSlot]    := LINT_TO_UDINT(RuntimeMs);
                GVL.EvKind[EvSlot] := 10;
                GVL.EvVal[EvSlot]  := TO_UDINT(MessageUnpackerFb.TryReadINT64('code', 0));
                GVL.EvHead := GVL.EvHead + 1;
                ShouldReturn := (CommandId <> 0);
                CommandAck := TRUE;
                ResponsePacketPointer^.ele_count := 0;
            ELSE
                // Unknown SYS command -- emit NAK so the TS caller doesn't hang on timeout.
                ShouldReturn := TRUE;
                CommandAck := FALSE;
                ResponsePacketPointer^.ele_count := 0;
                MessagePackerFb.PackKvStr(ADR('err'), ADR('unknown SYS cmd'));
            END_IF;

            IF SrcSys THEN GVL.sysinfo_buf_ridx.ConsumeTail(); ELSE GVL.minfo_buf_ridx.ConsumeTail(); END_IF;

            IF ShouldReturn = TRUE THEN // return ack/nak with id
                CommitReplySlotOrScratch(CommandId, CommandAck, ADR(GVL.ReMpDropCount_Reply));
            END_IF;
            SysDrainIter := SysDrainIter + 1;

        ELSIF (PacketType <> 'M') THEN
            // Tail is neither SYS nor 'M'. Most likely a caller forgot to set
            // type:'M' on a motion command (closed B1 ghost: SetCoord1 sent
            // as {cmd:'SetCoord1'} with no type sat at the tail forever,
            // blocked PINGs, supervisor tripped at 5s). NAK inline so a
            // future caller fails fast instead of "stalling" -- and so any
            // SYS packets queued behind it can drain in this same loop.
            // Drop-newest: skip the NAK entirely if the reply ring is full
            // rather than overwrite a slot the Comm-task consumer is reading.
            IF TryAcquireReplySlot(ADR(GVL.ReMpDropCount_Reply)) THEN
                MessagePackerFb.PackKvStr(ADR('err'), ADR('missing_type_field'));
                MessagePackerFb.PackKvStr(ADR('err_got_type'), ADR(PacketType));
                CommitReplySlotAck(CommandId, FALSE);
            END_IF;

            IF SrcSys THEN GVL.sysinfo_buf_ridx.ConsumeTail(); ELSE GVL.minfo_buf_ridx.ConsumeTail(); END_IF;
            GVL.MissingTypeFieldNakCount := GVL.MissingTypeFieldNakCount + 1;

            PacketType := '';
            CommandType := '';
            ResponsePacketPointer := 0;
            SysDrainIter := SysDrainIter + 1;

        ELSE
            // Tail is type:'M' (motion). When FSM isn't Ready, NAK it inline
            // with 'group_not_ready' so the next iteration can see any SYS
            // packets queued behind it. When Ready, EXIT and let
            // ProcessMotionPacket handle the tail.
            IF NOT AxisGroupReady THEN
                // Drop-newest: skip the NAK entirely if the reply ring is full.
                IF TryAcquireReplySlot(ADR(GVL.ReMpDropCount_Reply)) THEN
                    MessagePackerFb.PackKvStr(ADR('err'), ADR('group_not_ready'));
                    CommitReplySlotAck(CommandId, FALSE);
                END_IF;

                IF SrcSys THEN GVL.sysinfo_buf_ridx.ConsumeTail(); ELSE GVL.minfo_buf_ridx.ConsumeTail(); END_IF;
                GVL.GroupNotReadyNakCount := GVL.GroupNotReadyNakCount + 1;

                PacketType := '';
                CommandType := '';
                ResponsePacketPointer := 0;
                SysDrainIter := SysDrainIter + 1;
            ELSE
                EXIT;
            END_IF;
        END_IF;
    END_WHILE;
