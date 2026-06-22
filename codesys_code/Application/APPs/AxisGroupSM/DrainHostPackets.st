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
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Drains GVL.minfo_buf_ridx (host->PLC packet ring). Per-iteration:
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

    WHILE (GVL.minfo_buf_ridx.Size() > 0) AND (SysDrainIter < SYS_DRAIN_MAX_PER_SCAN) DO
        MessageBufferPointer := ADR(GVL.minfo_buf[GVL.minfo_buf_ridx.GetTail()][0]);
        MessageUnpackerFb.Init(ADR(MessageBufferPointer[1]), MessageBufferPointer[0]);
        CommandId := TO_DINT(MessageUnpackerFb.TryReadINT64('id', - 1));
        PacketType := MessageUnpackerFb.TryReadString('type', '');
        CommandType := MessageUnpackerFb.TryReadString('cmd', '');
        ProtocolVersionGot := MessageUnpackerFb.TryReadINT64('protocol_version', 0);

        // Any inbound packet -- not just SYS/PING -- proves the client is
        // alive. Pre-fix only PING refreshed LastUiPingMs, so a UI sending
        // a steady G1 stream (no PING) still tripped the A3 supervisor
        // mid-motion. Refresh on every drain-loop entry so the watchdog
        // tracks "client is talking" rather than "client is pinging".
        // The PING handler's own refresh below is now redundant but kept
        // alongside the PingMaxGapMs / UiPingCount stats it owns.
        GVL.LastUiPingMs := RuntimeMs;

        // W1 A5: if the client declared a version and it doesn't match,
        // NAK before any dispatch. 0 (absent / legacy) is allowed through.
        IF (ProtocolVersionGot <> 0 AND TO_UDINT(ProtocolVersionGot) <> GVL.PROTOCOL_VERSION) THEN
            // Drop-newest on overflow (see UpdateMotionProgress). The
            // minfo_buf ConsumeTail and counter still run so a full reply
            // ring doesn't infinitely re-NAK the same packet.
            IF (GVL.reMP_info_ridx.Space() > 0) THEN
                ResponsePacketPointer := ADR(GVL.reMP_info_arr[GVL.reMP_info_ridx.GetHead()]);
                MsgPakInfoClear(ResponsePacketPointer^);
                ResponsePacketPointer^.ele_count := 4;
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('protocol_version_mismatch'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err_got'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := ProtocolVersionGot);
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('id'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := CommandId);
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('ack'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackBool(pStart := ResponsePacketPointer^.whead, bValue := FALSE);
                MsgPakInfoWrapup(ResponsePacketPointer^);
                GVL.reMP_info_ridx.PushHead();
            ELSE
                GVL.ReMpDropCount := GVL.ReMpDropCount + 1;
                GVL.ReMpDropCount_Reply := GVL.ReMpDropCount_Reply + 1;
            END_IF;

            GVL.minfo_buf_ridx.ConsumeTail();
            GVL.ProtocolVersionMismatchCount := GVL.ProtocolVersionMismatchCount + 1;

            // Neutralise downstream dispatch in case loop exits next.
            PacketType := '';
            CommandType := '';
            ResponsePacketPointer := 0;
            SysDrainIter := SysDrainIter + 1;

        ELSIF (PacketType = 'SYS') THEN
            // reMP overflow: drop-newest. Producer must NOT ConsumeTail
            // (Comm task is sole consumer; two consumers race on _tail).
            // The SYS handler block below has many branches that all pack
            // into ResponsePacketPointer^; route to GVL.reMP_info_scratch
            // when full so the inline pack code stays unchanged. The
            // PushHead at the bottom of this branch is gated on
            // SlotAvailable.
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

            IF (CommandType = 'GA_EV') THEN
                ShouldReturn := TRUE;
                GaEvRaw := MessageUnpackerFb.TryReadINT64('ev', -1);
                // 2026-06-23 code-review fix: validate ev before posting.
                // Pre-fix: TO_UDINT(-1) wrapped to 16#FFFFFFFF, Transition
                // no-op'd, but reply still ACKed -- host couldn't tell the
                // FSM did nothing. Range check covers E_RobotEvent's UDINT
                // enum: NONE=0..EV_ER=11. Out-of-range (including the -1
                // missing-key sentinel) is rejected with err='unknown_event'.
                IF (GaEvRaw < 1) OR (GaEvRaw > 11) THEN
                    CommandAck := FALSE;
                    GVL.UnknownEventNakCount := GVL.UnknownEventNakCount + 1;
                    ResponsePacketPointer^.ele_count := 2;
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('ack'));
                    ResponsePacketPointer^.whead := MessagePackerFb.PackBool(pStart := ResponsePacketPointer^.whead, bValue := FALSE);
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err'));
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('unknown_event'));
                ELSE
                    CommandAck := TRUE;
                    InputEvent := TO_UDINT(GaEvRaw);
                    AxisGroupManagerFb.TRANSITION(
                        eEvent := InputEvent
                    );

                    ResponsePacketPointer^.ele_count := 4;
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('st'));
                    ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := AxisGroupManagerFb._eState);
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('st_str'));
                    StateString := TO_STRING(AxisGroupManagerFb._eState);
                    // NOTE: PrevAxisGroupState is intentionally NOT updated here.
                    // The ST_CHG push-event detector above at line ~255 owns that
                    // variable; pre-updating it would suppress the ST_CHG event
                    // for any transition triggered via SYS/GA_EV (verified live
                    // 2026-04-24: UnInited->Powering was silently swallowed).
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR(StateString));
                    // Error latch: empty string + 0 when no error has been recorded
                    // since the last EV_RESET -> UnInited cycle.
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err_src'));
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR(GVL.LastErrorSource));
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err_id'));
                    ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.LastErrorID));
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
                ResponsePacketPointer^.ele_count := 2;
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('pong'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackBool(pStart := ResponsePacketPointer^.whead, bValue := TRUE);
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('runtime_ms'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := RuntimeMs);
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
                ResponsePacketPointer^.ele_count := 16;
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('st'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := AxisGroupManagerFb._eState);
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('st_str'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR(StateString));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err_src'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR(GVL.LastErrorSource));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err_id'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.LastErrorID));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('motion_buffer_size'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := TO_LINT(MotionBufferSize));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('movement_id'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := TO_LINT(GroupReadPositionFb.MovementId));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('runtime_ms'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := RuntimeMs);
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('coord_set'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackBool(pStart := ResponsePacketPointer^.whead, bValue := GVL.CoordSystemConfigured);
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('axes_err_mask'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(AxesErrMask));
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
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('axes_err_id'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackArrayHeader(pStart := ResponsePacketPointer^.whead, uiCount := 4);
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := UINT_TO_DINT(GVL.LastAxesErrorIDs[0]));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := UINT_TO_DINT(GVL.LastAxesErrorIDs[1]));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := UINT_TO_DINT(GVL.LastAxesErrorIDs[2]));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := UINT_TO_DINT(GVL.LastAxesErrorIDs[3]));
                // axes_labels : self-describing axis order, same indexing as
                // axes_err_id / axes_err_mask bits 0..3. UI consumes this
                // instead of hardcoding labels, collapsing the four-site
                // axes_err_id ordering coupling (PLC pack / GVL / mask bits /
                // UI labels) down to a single source of truth on the PLC.
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('axes_labels'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackArrayHeader(pStart := ResponsePacketPointer^.whead, uiCount := 4);
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('EAxis0 (arm1)'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('EAxis1 (arm2)'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('EAxis2 (arm3)'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('reelpullmotor'));
                // reel_pos : reelpullmotor.fActPosition (REAL, axis-scaled
                // user units -- mm under current GenericDSP402 scaling).
                // Driven by decisions_2026-06-22.md §4 (1): renderer uses
                // this on resume to derive the carrier-tape cell number
                // (cell = round((reel_pos - origin) / pitch)). PLC stays
                // generic and reports raw position; the "origin" and
                // "pitch" semantics live entirely on the host.
                // Retention across PLC restart scenarios verified by
                // probe_reel_retain (STOP+START path conclusive).
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('reel_pos'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := reelpullmotor.fActPosition);
                // scratchpad : nested map carrying the host-owned resume
                // cursor. PLC pipes the retained Scratchpad_v1 struct out
                // verbatim; renderer compares boot_epoch vs GVL.BootEpochCount
                // (renderer also reads BootEpochCount separately?) to detect
                // staleness. See decisions_2026-06-22.md §4 (2) + B.6.
                // 7 nested keys.
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('scratchpad'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackMapHeader(pStart := ResponsePacketPointer^.whead, uiCount := 7);
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('schema_version'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.ScratchpadV1.SchemaVersion));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('plan_id'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := UDINT_TO_DINT(GVL.ScratchpadV1.PlanId));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('plan_index'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := UDINT_TO_DINT(GVL.ScratchpadV1.PlanIndex));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('intent_kind'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.ScratchpadV1.IntentKind));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('intent_movement_id'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := UDINT_TO_DINT(GVL.ScratchpadV1.IntentMovementId));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('last_vision_pulse'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := UDINT_TO_DINT(GVL.ScratchpadV1.LastVisionPulse));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('boot_epoch'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := UDINT_TO_DINT(GVL.ScratchpadV1.BootEpoch));
                // boot_epoch_now : current GVL.BootEpochCount, top-level peer
                // of scratchpad. Renderer compares scratchpad.boot_epoch
                // (captured at last write) against this; mismatch -> PLC
                // restarted since the write -> scratchpad stale.
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('boot_epoch_now'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := UDINT_TO_DINT(GVL.BootEpochCount));
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
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('last_completed_movement_id'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := TO_LINT(PrevCompletedMovementId));
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
                    ResponsePacketPointer^.ele_count := 2;
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('ack'));
                    ResponsePacketPointer^.whead := MessagePackerFb.PackBool(pStart := ResponsePacketPointer^.whead, bValue := FALSE);
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err'));
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('partial_scratchpad'));
                ELSE
                    CommandAck := TRUE;
                    GVL.ScratchpadV1.PlanId           := TO_UDINT(ScratchpadPlanIdRaw);
                    GVL.ScratchpadV1.PlanIndex        := TO_UDINT(ScratchpadPlanIndexRaw);
                    GVL.ScratchpadV1.IntentKind       := TO_USINT(ScratchpadIntentKindRaw);
                    GVL.ScratchpadV1.IntentMovementId := TO_UDINT(ScratchpadIntentMidRaw);
                    GVL.ScratchpadV1.LastVisionPulse  := TO_UDINT(ScratchpadLastVisionPulseRaw);
                    GVL.ScratchpadV1.SchemaVersion := 1;
                    GVL.ScratchpadV1.BootEpoch := GVL.BootEpochCount;
                    ResponsePacketPointer^.ele_count := 1;
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('ack'));
                    ResponsePacketPointer^.whead := MessagePackerFb.PackBool(pStart := ResponsePacketPointer^.whead, bValue := TRUE);
                END_IF
            ELSIF (CommandType = 'RESET_DBG_INFO') THEN
                // SYS-side reset: clears comm-stability counters without
                // requiring FSM=Ready (the motion-side RESET_DBG_INFO at the
                // bottom of this file is a legacy branch and only fires from
                // the M dispatcher path). Generic comm hygiene; lets the UI
                // dashboard rebaseline at any time.
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
                ResponsePacketPointer^.ele_count := 1;
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('reset'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackBool(pStart := ResponsePacketPointer^.whead, bValue := TRUE);
            ELSIF (CommandType = 'VERSION') THEN
                // Build provenance: lets an external tool verify the running
                // PLC matches a repo commit without IDE assistance. Stamped by
                // codesys_scripts/jobs/stamp_build_info.py before import_all.
                ShouldReturn := TRUE;
                CommandAck := TRUE;
                ResponsePacketPointer^.ele_count := 3;
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('git_sha'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR(GVL.BUILD_GIT_SHA));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('build_ts_ms'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := GVL.BUILD_TS_MS);
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('runtime_ms'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := RuntimeMs);
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
                ResponsePacketPointer^.ele_count := 27;
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('remp_overflow_drop'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.ReMpOverflowDropCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('runtime_ms'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := RuntimeMs);
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('sm_scans'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.AxisGroupSMScans));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('remp_drop'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.ReMpDropCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('overlen_drop'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.OverlenDropCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('send_stall_drop'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.SendStallDropCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('group_not_ready_nak'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.GroupNotReadyNakCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('missing_type_nak'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.MissingTypeFieldNakCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('coord_not_cfg_nak'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.CoordNotConfiguredNakCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('proto_mismatch_nak'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.ProtocolVersionMismatchCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('idle_reset'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.IdleResetCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('read_err_reset'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.ReadErrorResetCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('parser_err_reset'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.ParserErrorResetCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('ui_ping_count'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.UiPingCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('ui_hb_stale_count'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.UiHeartbeatStaleCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('ping_max_gap_ms'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.PingMaxGapMs));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('last_ui_ping_ms'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := GVL.LastUiPingMs);
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('st_chg_event_count'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.StateChangeEventCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('self_reentry'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.SelfReentryCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('pending_stchg_drop'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.PendingStChgDropCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('pending_movedone_drop'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.PendingMoveDoneDropCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('remp_drop_reply'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.ReMpDropCount_Reply));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('remp_drop_trig'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.ReMpDropCount_TriggerEvent));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('dupe_cmd'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.DupeCommandCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('io_cmd_count'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(IoCommandCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('io_trig_count'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(IoTriggerCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('flyevent_avail'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(FlyEventAvailableCount));
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
                ShouldReturn := TRUE;
                BindReject := FALSE;
                BindRejectReason := '';

                BindKind := MessageUnpackerFb.TryReadString('belt_kind', 'linear');

                IF MotionBufferSize > 0 THEN
                    BindReject := TRUE;
                    BindRejectReason := 'coord_bind_busy';
                    GVL.Coord1BindBusyNakCount := GVL.Coord1BindBusyNakCount + 1;
                END_IF

                IF NOT BindReject AND BindKind <> 'linear' AND BindKind <> '' THEN
                    BindReject := TRUE;
                    BindRejectReason := 'belt_kind_not_yet_supported';
                END_IF

                IF NOT BindReject AND GVL.Coord1Bound THEN
                    BindReject := TRUE;
                    BindRejectReason := 'rebind_requires_unbind';
                END_IF

                IF NOT BindReject THEN
                    // Read top-level scalars (path-based, defaulting to 0).
                    GVL.Coord1RefPulse   := TO_DINT(MessageUnpackerFb.TryReadINT64('ref_pulse', 0));
                    GVL.Coord1RefXyz[0]  := TO_LREAL(MessageUnpackerFb.TryReadREAL('ref_xyz[0]', 0.0));
                    GVL.Coord1RefXyz[1]  := TO_LREAL(MessageUnpackerFb.TryReadREAL('ref_xyz[1]', 0.0));
                    GVL.Coord1RefXyz[2]  := TO_LREAL(MessageUnpackerFb.TryReadREAL('ref_xyz[2]', 0.0));
                    GVL.Coord1Scale[0]   := TO_LREAL(MessageUnpackerFb.TryReadREAL('scale[0]', 0.0));
                    GVL.Coord1Scale[1]   := TO_LREAL(MessageUnpackerFb.TryReadREAL('scale[1]', 0.0));
                    GVL.Coord1Scale[2]   := TO_LREAL(MessageUnpackerFb.TryReadREAL('scale[2]', 0.0));

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

                // Phase 4 step 2 belt-direction gates: belt must move
                // along WCS +X (Scale[0] != 0, Scale[1] = Scale[2] = 0).
                // Non-axial / 2D belt support means a non-zero
                // ConveyorBeltOrigin.A rotation -- added when needed.
                IF NOT BindReject AND (GVL.Coord1Scale[1] <> 0.0 OR GVL.Coord1Scale[2] <> 0.0) THEN
                    BindReject := TRUE;
                    BindRejectReason := 'belt_direction_not_axis_x';
                END_IF
                IF NOT BindReject AND GVL.Coord1Scale[0] = 0.0 THEN
                    BindReject := TRUE;
                    BindRejectReason := 'belt_scale_x_required';
                END_IF

                IF BindReject THEN
                    CommandAck := FALSE;
                    ResponsePacketPointer^.ele_count := 1;
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err'));
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR(BindRejectReason));
                ELSE
                    // Commit (Phase 4 step 2): set the calibration + belt
                    // origin first, then flip Coord1Bound TRUE -- the per-
                    // scan tcb() at the top of the next scan picks up the
                    // rising edge with the new origin already in place.
                    // (SYS drain runs after the per-scan recompute, so an
                    // edge fired this scan would use stale data.)
                    Coord1PulseToMm := 1.0 / GVL.Coord1Scale[0];
                    Coord1BeltOriginRef.X := GVL.Coord1RefXyz[0] - TO_LREAL(GVL.Coord1RefPulse) * Coord1PulseToMm;
                    Coord1BeltOriginRef.Y := GVL.Coord1RefXyz[1];
                    Coord1BeltOriginRef.Z := GVL.Coord1RefXyz[2];
                    Coord1BeltOriginRef.A := 0.0;
                    Coord1BeltOriginRef.B := 0.0;
                    Coord1BeltOriginRef.C := 0.0;

                    GVL.Coord1CamMode     := GVL.COORD1_MODE_LINEAR;
                    GVL.Coord1CamCount    := 0;
                    GVL.Coord1Bound       := TRUE;
                    GVL.Coord1BindCount   := GVL.Coord1BindCount + 1;
                    CommandAck := TRUE;
                    ResponsePacketPointer^.ele_count := 2;
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('mode'));
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('linear'));
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('n'));
                    ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := 0);
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
                ResponsePacketPointer^.ele_count := 30;
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('bound'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackBool(pStart := ResponsePacketPointer^.whead, bValue := GVL.Coord1Bound);
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('mode'));
                IF GVL.Coord1CamMode = GVL.COORD1_MODE_TABLE THEN
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('table'));
                ELSE
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('linear'));
                END_IF
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('cam_n'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.Coord1CamCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('cam_first'));
                IF GVL.Coord1CamCount > 0 THEN
                    ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := GVL.Coord1CamMaster[0]);
                ELSE
                    ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := 0);
                END_IF
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('cam_last'));
                IF GVL.Coord1CamCount > 0 THEN
                    ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := GVL.Coord1CamMaster[GVL.Coord1CamCount - 1]);
                ELSE
                    ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := 0);
                END_IF
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('pulse_raw'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := GVL.ConveyorPulseRaw);
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('ref_pulse'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := GVL.Coord1RefPulse);
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('ref_x'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := TO_REAL(GVL.Coord1RefXyz[0]));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('ref_y'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := TO_REAL(GVL.Coord1RefXyz[1]));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('ref_z'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := TO_REAL(GVL.Coord1RefXyz[2]));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('scale_x'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := TO_REAL(GVL.Coord1Scale[0]));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('scale_y'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := TO_REAL(GVL.Coord1Scale[1]));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('scale_z'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := TO_REAL(GVL.Coord1Scale[2]));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('origin_x'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := TO_REAL(GVL.Coord1OriginNow[0]));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('origin_y'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := TO_REAL(GVL.Coord1OriginNow[1]));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('origin_z'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := TO_REAL(GVL.Coord1OriginNow[2]));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('bind_count'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.Coord1BindCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('bind_busy_nak'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.Coord1BindBusyNakCount));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('bad_table_nak'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := TO_DINT(GVL.Coord1BadTableNakCount));
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
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('kernel_origin_x'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := TO_REAL(Pcs1KernelTransform.X));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('kernel_origin_y'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := TO_REAL(Pcs1KernelTransform.Y));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('kernel_origin_z'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := TO_REAL(Pcs1KernelTransform.Z));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('live_kernel_origin_x'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := TO_REAL(Pcs1KernelTransform.X + ConveyorEncoderAxis.fActPosition));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('live_kernel_origin_y'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := TO_REAL(Pcs1KernelTransform.Y));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('live_kernel_origin_z'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := TO_REAL(Pcs1KernelTransform.Z));
                // Live arm TCP position (WCS). Folded into GET_COORD1_DEBUG
                // so the viz tool can sample arm motion via SYS path
                // without being blocked by the motion FSM gate (which
                // NAKs all type:M packets after a virtual-axis self-halt).
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('arm_x'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := TO_REAL(ReadPosition.c.x));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('arm_y'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := TO_REAL(ReadPosition.c.y));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('arm_z'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := TO_REAL(ReadPosition.c.z));
                // Phase 4 step 2 probe: SM3 motion progress for the move
                // currently in flight. With CoordSystem := PCS_1 on a
                // belt-tracking move, this is the data point we need to
                // answer "does progress 0->1 mean tracking 0->1".
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('mv_id'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := TO_LINT(CurrentMovementId));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('mv_progress'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := CurrentMovementIdProgress);
            ELSE
                // Unknown SYS command -- emit NAK so the TS caller doesn't hang on timeout.
                ShouldReturn := TRUE;
                CommandAck := FALSE;
                ResponsePacketPointer^.ele_count := 1;
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('unknown SYS cmd'));
            END_IF;

            GVL.minfo_buf_ridx.ConsumeTail();

            IF ShouldReturn = TRUE THEN // return ack/nak with id
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('id'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := CommandId);

                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('ack'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackBool(pStart := ResponsePacketPointer^.whead, bValue := CommandAck);

                ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 2;
                MsgPakInfoWrapup(ResponsePacketPointer^);
                // Drop-newest: only commit if the real ring slot was reserved.
                // If SlotAvailable=FALSE we packed into the scratch slot and
                // must NOT PushHead (would corrupt the slot a consumer is
                // currently reading). See GVL.reMP_info_scratch note.
                IF SlotAvailable THEN
                    GVL.reMP_info_ridx.PushHead();
                ELSE
                    GVL.ReMpDropCount := GVL.ReMpDropCount + 1;
                    GVL.ReMpDropCount_Reply := GVL.ReMpDropCount_Reply + 1;
                END_IF;
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
            IF (GVL.reMP_info_ridx.Space() > 0) THEN
                ResponsePacketPointer := ADR(GVL.reMP_info_arr[GVL.reMP_info_ridx.GetHead()]);
                MsgPakInfoClear(ResponsePacketPointer^);
                ResponsePacketPointer^.ele_count := 4;
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('missing_type_field'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err_got_type'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR(PacketType));
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('id'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := CommandId);
                ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('ack'));
                ResponsePacketPointer^.whead := MessagePackerFb.PackBool(pStart := ResponsePacketPointer^.whead, bValue := FALSE);
                MsgPakInfoWrapup(ResponsePacketPointer^);
                GVL.reMP_info_ridx.PushHead();
            ELSE
                GVL.ReMpDropCount := GVL.ReMpDropCount + 1;
                GVL.ReMpDropCount_Reply := GVL.ReMpDropCount_Reply + 1;
            END_IF;

            GVL.minfo_buf_ridx.ConsumeTail();
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
                IF (GVL.reMP_info_ridx.Space() > 0) THEN
                    ResponsePacketPointer := ADR(GVL.reMP_info_arr[GVL.reMP_info_ridx.GetHead()]);
                    MsgPakInfoClear(ResponsePacketPointer^);
                    ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 3;
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('err'));
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('group_not_ready'));
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('id'));
                    ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := CommandId);
                    ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := ADR('ack'));
                    ResponsePacketPointer^.whead := MessagePackerFb.PackBool(pStart := ResponsePacketPointer^.whead, bValue := FALSE);
                    MsgPakInfoWrapup(ResponsePacketPointer^);
                    GVL.reMP_info_ridx.PushHead();
                ELSE
                    GVL.ReMpDropCount := GVL.ReMpDropCount + 1;
                    GVL.ReMpDropCount_Reply := GVL.ReMpDropCount_Reply + 1;
                END_IF;

                GVL.minfo_buf_ridx.ConsumeTail();
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
