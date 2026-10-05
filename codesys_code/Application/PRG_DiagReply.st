PROGRAM PRG_DiagReply
VAR_INPUT
    // Request: one SYS packet copied in by TCP_MSGPAK_Server (Comm task),
    // ReqLen > 0 means "answer it this call"; cleared when done. Inputs,
    // so the caller may write them.
    ReqBuf  : ARRAY[0..255] OF BYTE;
    ReqLen  : UDINT;
END_VAR
VAR
    // Reply, ready for fbMyServer.Send when OutLen > 0 (one call = one reply).
    OutBuf  : ARRAY[0..2047] OF BYTE;
    OutLen  : UDINT;
    DiagInfo  : MsgPakInfo;
    xInfoInit : BOOL;

    // Same names as in AxisGroupSM so the handler code moved here on
    // 2026-10-05 reads unchanged (own instances: this is another task).
    MessagePackerFb       : FB_MpPacker;
    MessageUnpackerFb     : FB_MpUnpacker;
    ResponsePacketPointer : POINTER TO MsgPakInfo;
    CommandId   : DINT;
    CommandType : STRING;
    CommandAck  : BOOL;
    ShouldReturn : BOOL;

    HandledCount : UDINT;     // replies built
    UnknownCount : UDINT;     // a command routed here without a handler

    // Scratch (was AxisGroupSM.DrainHostPackets VAR).
    EcTrStr : STRING(255);    // EC_STATS trl / trh
    EcSnapK : INT;
    EcObjJ  : INT;
    EcObjKey : STRING(7);
    EcTrI   : INT;
    EcEvtFrom : UDINT;
    EcEvtI  : UDINT;
    EcEvtTo : UDINT;    // DWELL: end of the stops in ev (2026-10-05)
    // TASK_STATS scratch.
    TsH     : __XWORD;        // RTS_IEC_HANDLE
    TsR     : UDINT;          // RTS_IEC_RESULT, 0 = ERR_OK
    TsP     : POINTER TO CmpIecTask.Task_Info2;
    TsN     : INT;
    TsReset : BOOL;
    TsKey   : STRING(31);
    TsName  : STRING(63);
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Read-only diagnostics answered in the Comm task (2026-10-05). These
// commands only read GVL / AxisGroupSM state (a few set a reset flag that
// the EtherCAT task consumes), and their replies are long (EC_STATS ~1 KB,
// TASK_STATS, DEM_STATS snap): built in the 1 ms EtherCAT task they could
// make a scan overrun. TCP_MSGPAK_Server copies the packet into ReqBuf and
// calls this program, then sends OutBuf itself, so the reply ring
// (reMP_info) keeps its single producer. Not in a task of its own.
//
// The handler bodies are the ones that lived in AxisGroupSM.DrainHostPackets;
// AxisGroupSM.* reads are a torn-read risk only in theory (diagnostics).
OutLen := 0;
IF ReqLen = 0 THEN
    RETURN;
END_IF;
IF NOT xInfoInit THEN
    DiagInfo := MsgPakInfoInit(ADR(OutBuf), 2048, TRUE);
    xInfoInit := TRUE;
END_IF;
MsgPakInfoClear(DiagInfo);
ResponsePacketPointer := ADR(DiagInfo);
MessagePackerFb.BindSlot(ResponsePacketPointer);
MessageUnpackerFb.Init(ADR(ReqBuf), ReqLen);
CommandId   := TO_DINT(MessageUnpackerFb.TryReadINT64('id', 0));
CommandType := MessageUnpackerFb.TryReadString('cmd', '');
CommandAck  := FALSE;
ShouldReturn := FALSE;

    IF (CommandType = 'SETTLE_TRACE') THEN
        // The last stop trace (GVL.Tr*): seq, at (PLC ms of the stop),
        // ev = up to 4 samples from index from:i (0..399, 100 = the
        // stop) as cx:cy:cz:ax:ay:az (mm), comma separated.
        ShouldReturn := TRUE;
        CommandAck := TRUE;
        ResponsePacketPointer^.ele_count := 0;
        EcTrI := LIMIT(0, LINT_TO_INT(MessageUnpackerFb.TryReadINT64('from', 0)), 399);
        MessagePackerFb.PackKvLint(ADR('seq'), UDINT_TO_LINT(GVL.TrSeq));
        MessagePackerFb.PackKvLint(ADR('at'), UDINT_TO_LINT(GVL.TrAtMs));
        EcTrStr := '';
        FOR EcObjJ := EcTrI TO MIN(EcTrI + 3, 399) DO
            EcTrStr := CONCAT(EcTrStr, REAL_TO_STRING(GVL.TrCX[EcObjJ]));
            EcTrStr := CONCAT(EcTrStr, ':');
            EcTrStr := CONCAT(EcTrStr, REAL_TO_STRING(GVL.TrCY[EcObjJ]));
            EcTrStr := CONCAT(EcTrStr, ':');
            EcTrStr := CONCAT(EcTrStr, REAL_TO_STRING(GVL.TrCZ[EcObjJ]));
            EcTrStr := CONCAT(EcTrStr, ':');
            EcTrStr := CONCAT(EcTrStr, REAL_TO_STRING(GVL.TrAX[EcObjJ]));
            EcTrStr := CONCAT(EcTrStr, ':');
            EcTrStr := CONCAT(EcTrStr, REAL_TO_STRING(GVL.TrAY[EcObjJ]));
            EcTrStr := CONCAT(EcTrStr, ':');
            EcTrStr := CONCAT(EcTrStr, REAL_TO_STRING(GVL.TrAZ[EcObjJ]));
            EcTrStr := CONCAT(EcTrStr, ',');
        END_FOR;
        MessagePackerFb.PackKvStr(ADR('ev'), ADR(EcTrStr));
    ELSIF (CommandType = 'SETTLE') THEN
        // Settling-time log (GVL.Settle*): n = stops so far, tol =
        // tolerance mm, ev = up to 20 stops from index from:i as
        // at_ms:settle_ms:err_at_stop_um. reset:1 clears it.
        ShouldReturn := TRUE;
        CommandAck := TRUE;
        ResponsePacketPointer^.ele_count := 0;
        IF MessageUnpackerFb.TryReadINT64('reset', 0) <> 0 THEN
            GVL.SettleN := 0;
        END_IF;
        EcEvtFrom := LINT_TO_UDINT(MessageUnpackerFb.TryReadINT64('from', 0));
        MessagePackerFb.PackKvLint(ADR('n'), UDINT_TO_LINT(GVL.SettleN));
        MessagePackerFb.PackKvReal(ADR('tol'), LREAL_TO_REAL(GVL.SettleTolMm));
        EcTrStr := '';
        EcEvtI := EcEvtFrom;
        // rows <= 33 characters: none after 220, so STRING(255) is never cut
        WHILE (EcEvtI < GVL.SettleN) AND (EcEvtI < EcEvtFrom + 20) AND (LEN(EcTrStr) <= 220) DO
            EcTrStr := CONCAT(EcTrStr, UDINT_TO_STRING(GVL.SettleAtMs[EcEvtI MOD 256]));
            EcTrStr := CONCAT(EcTrStr, ':');
            EcTrStr := CONCAT(EcTrStr, UDINT_TO_STRING(GVL.SettleMs[EcEvtI MOD 256]));
            EcTrStr := CONCAT(EcTrStr, ':');
            EcTrStr := CONCAT(EcTrStr, UDINT_TO_STRING(GVL.SettleErrUm[EcEvtI MOD 256]));
            EcTrStr := CONCAT(EcTrStr, ',');
            EcEvtI := EcEvtI + 1;
        END_WHILE;
        MessagePackerFb.PackKvStr(ADR('ev'), ADR(EcTrStr));
        MessagePackerFb.PackKvLint(ADR('next'), UDINT_TO_LINT(EcEvtI));
    ELSIF (CommandType = 'DWELL') THEN
        // Every-stop log (GVL.Dw*): n = stops so far, tol mm, hist =
        // settle time histogram (edges 1 2 5 10 15 20 50 ms, last =
        // not settled), emax = max error when leaving (um), ev = up
        // to 8 stops from index from:i as at_ms:dwell_ms:settle_ms:
        // err_stop_um:err_leave_um:err_5ms_um:err_10ms_um (settle 4294967295
        // = not settled; fields 6 and 7 since 2026-10-05); evq = the same
        // stops as buf:hostq:cool_ms:z_um:since_acc_ms (GVL.DwBuf / DwHostQ /
        // DwCoolMs / DwZUm / DwSinceAccMs, 2026-10-05; a separate string so ev
        // stays within 255).
        // reset:1 clears it.
        ShouldReturn := TRUE;
        CommandAck := TRUE;
        ResponsePacketPointer^.ele_count := 0;
        IF MessageUnpackerFb.TryReadINT64('reset', 0) <> 0 THEN
            GVL.DwN := 0;
            GVL.DwErrLeaveMaxUm := 0;
            FOR EcObjJ := 0 TO 8 DO
                GVL.DwHist[EcObjJ] := 0;
            END_FOR;
        END_IF;
        EcEvtFrom := LINT_TO_UDINT(MessageUnpackerFb.TryReadINT64('from', 0));
        MessagePackerFb.PackKvLint(ADR('n'), UDINT_TO_LINT(GVL.DwN));
        MessagePackerFb.PackKvReal(ADR('tol'), LREAL_TO_REAL(GVL.SettleTolMm));
        MessagePackerFb.PackKvLint(ADR('emax'), UDINT_TO_LINT(GVL.DwErrLeaveMaxUm));
        EcTrStr := '';
        FOR EcObjJ := 0 TO 8 DO
            EcTrStr := CONCAT(EcTrStr, UDINT_TO_STRING(GVL.DwHist[EcObjJ]));
            EcTrStr := CONCAT(EcTrStr, ',');
        END_FOR;
        MessagePackerFb.PackKvStr(ADR('hist'), ADR(EcTrStr));
        EcTrStr := '';
        EcEvtI := EcEvtFrom;
        // At most 8 stops, and none that could be cut: a row is <= ~60
        // characters, so no new row after 180 (STRING(255), 2026-10-05
        // review). evq covers the same stops; next = the index to ask next.
        WHILE (EcEvtI < GVL.DwN) AND (EcEvtI < EcEvtFrom + 8) AND (LEN(EcTrStr) <= 180) DO
            EcTrStr := CONCAT(EcTrStr, UDINT_TO_STRING(GVL.DwAtMs[EcEvtI MOD 4096]));
            EcTrStr := CONCAT(EcTrStr, ':');
            EcTrStr := CONCAT(EcTrStr, UDINT_TO_STRING(GVL.DwDwellMs[EcEvtI MOD 4096]));
            EcTrStr := CONCAT(EcTrStr, ':');
            EcTrStr := CONCAT(EcTrStr, UDINT_TO_STRING(GVL.DwSettleMs[EcEvtI MOD 4096]));
            EcTrStr := CONCAT(EcTrStr, ':');
            EcTrStr := CONCAT(EcTrStr, UDINT_TO_STRING(GVL.DwErr0Um[EcEvtI MOD 4096]));
            EcTrStr := CONCAT(EcTrStr, ':');
            EcTrStr := CONCAT(EcTrStr, UDINT_TO_STRING(GVL.DwErrLeaveUm[EcEvtI MOD 4096]));
            EcTrStr := CONCAT(EcTrStr, ':');
            EcTrStr := CONCAT(EcTrStr, UDINT_TO_STRING(GVL.DwErr5Um[EcEvtI MOD 4096]));
            EcTrStr := CONCAT(EcTrStr, ':');
            EcTrStr := CONCAT(EcTrStr, UDINT_TO_STRING(GVL.DwErr10Um[EcEvtI MOD 4096]));
            EcTrStr := CONCAT(EcTrStr, ',');
            EcEvtI := EcEvtI + 1;
        END_WHILE;
        MessagePackerFb.PackKvStr(ADR('ev'), ADR(EcTrStr));
        MessagePackerFb.PackKvLint(ADR('next'), UDINT_TO_LINT(EcEvtI));
        EcEvtTo := EcEvtI;
        EcTrStr := '';
        EcEvtI := EcEvtFrom;
        WHILE EcEvtI < EcEvtTo DO
            EcTrStr := CONCAT(EcTrStr, UINT_TO_STRING(GVL.DwBuf[EcEvtI MOD 4096]));
            EcTrStr := CONCAT(EcTrStr, ':');
            EcTrStr := CONCAT(EcTrStr, UINT_TO_STRING(GVL.DwHostQ[EcEvtI MOD 4096]));
            EcTrStr := CONCAT(EcTrStr, ':');
            EcTrStr := CONCAT(EcTrStr, INT_TO_STRING(GVL.DwCoolMs[EcEvtI MOD 4096]));
            EcTrStr := CONCAT(EcTrStr, ':');
            EcTrStr := CONCAT(EcTrStr, DINT_TO_STRING(GVL.DwZUm[EcEvtI MOD 4096]));
            EcTrStr := CONCAT(EcTrStr, ':');
            EcTrStr := CONCAT(EcTrStr, UDINT_TO_STRING(GVL.DwSinceAccMs[EcEvtI MOD 4096]));
            EcTrStr := CONCAT(EcTrStr, ',');
            EcEvtI := EcEvtI + 1;
        END_WHILE;
        MessagePackerFb.PackKvStr(ADR('evq'), ADR(EcTrStr));
    ELSIF (CommandType = 'FB_STATS') THEN
        // Feedback shock monitor (GVL.Fb*): per drive k, fd{k} =
        // histogram of |d2 actual position| (edges 1k 2k 5k 10k 20k
        // 50k 100k PUU), fdm{k} its max, ft{k} = histogram of |torque
        // change| (edges 5 10 20 50 100 200 500, 0.1 %), ftm{k} its
        // max, tq{k} = the torque now. reset:1 clears after the reply.
        ShouldReturn := TRUE;
        CommandAck := TRUE;
        ResponsePacketPointer^.ele_count := 0;
        FOR EcTrI := 0 TO 2 DO
            EcTrStr := '';
            FOR EcObjJ := 0 TO 7 DO
                EcTrStr := CONCAT(EcTrStr, UDINT_TO_STRING(GVL.FbD2Hist[EcTrI, EcObjJ]));
                EcTrStr := CONCAT(EcTrStr, ',');
            END_FOR;
            EcObjKey := CONCAT('fd', INT_TO_STRING(EcTrI));
            MessagePackerFb.PackKvStr(ADR(EcObjKey), ADR(EcTrStr));
            EcObjKey := CONCAT('fdm', INT_TO_STRING(EcTrI));
            MessagePackerFb.PackKvDint(ADR(EcObjKey), GVL.FbD2Max[EcTrI]);
            EcTrStr := '';
            FOR EcObjJ := 0 TO 7 DO
                EcTrStr := CONCAT(EcTrStr, UDINT_TO_STRING(GVL.FbTqHist[EcTrI, EcObjJ]));
                EcTrStr := CONCAT(EcTrStr, ',');
            END_FOR;
            EcObjKey := CONCAT('ft', INT_TO_STRING(EcTrI));
            MessagePackerFb.PackKvStr(ADR(EcObjKey), ADR(EcTrStr));
            EcObjKey := CONCAT('ftm', INT_TO_STRING(EcTrI));
            MessagePackerFb.PackKvDint(ADR(EcObjKey), INT_TO_DINT(GVL.FbTqMax[EcTrI]));
        END_FOR;
        MessagePackerFb.PackKvDint(ADR('tq0'), INT_TO_DINT(GVL.TorqueTap0));
        MessagePackerFb.PackKvDint(ADR('tq1'), INT_TO_DINT(GVL.TorqueTap1));
        MessagePackerFb.PackKvDint(ADR('tq2'), INT_TO_DINT(GVL.TorqueTap2));
        IF MessageUnpackerFb.TryReadINT64('reset', 0) <> 0 THEN
            GVL.FbReset := TRUE;
        END_IF;
    ELSIF (CommandType = 'ESP_SYNC') THEN
        // The EasyCAT SYNC0-interval log (GVL.SyncLog*): n = buckets
        // so far, now = the last deviation (ns), ev = up to 12
        // buckets from index from:i as ms:min:max (ns).
        ShouldReturn := TRUE;
        CommandAck := TRUE;
        ResponsePacketPointer^.ele_count := 0;
        EcEvtFrom := LINT_TO_UDINT(MessageUnpackerFb.TryReadINT64('from', 0));
        MessagePackerFb.PackKvLint(ADR('n'), UDINT_TO_LINT(GVL.SyncLogN));
        MessagePackerFb.PackKvDint(ADR('now'), INT_TO_DINT(GVL.EspSyncDevNs));
        EcTrStr := '';
        EcEvtI := EcEvtFrom;
        WHILE (EcEvtI < GVL.SyncLogN) AND (EcEvtI < EcEvtFrom + 12) DO
            EcTrStr := CONCAT(EcTrStr, UDINT_TO_STRING(GVL.SyncLogMs[EcEvtI MOD 4096]));
            EcTrStr := CONCAT(EcTrStr, ':');
            EcTrStr := CONCAT(EcTrStr, INT_TO_STRING(GVL.SyncLogMin[EcEvtI MOD 4096]));
            EcTrStr := CONCAT(EcTrStr, ':');
            EcTrStr := CONCAT(EcTrStr, INT_TO_STRING(GVL.SyncLogMax[EcEvtI MOD 4096]));
            EcTrStr := CONCAT(EcTrStr, ',');
            EcEvtI := EcEvtI + 1;
        END_WHILE;
        MessagePackerFb.PackKvStr(ADR('ev'), ADR(EcTrStr));
    ELSIF (CommandType = 'ESP_ARR') THEN
        // The EasyCAT frame-arrival log (GVL.ArrLog*): n = buckets so
        // far, now = the last arrival (us), ev = up to 12 buckets from
        // index from:i as ms:min:max (us), comma separated.
        ShouldReturn := TRUE;
        CommandAck := TRUE;
        ResponsePacketPointer^.ele_count := 0;
        EcEvtFrom := LINT_TO_UDINT(MessageUnpackerFb.TryReadINT64('from', 0));
        MessagePackerFb.PackKvLint(ADR('n'), UDINT_TO_LINT(GVL.ArrLogN));
        MessagePackerFb.PackKvDint(ADR('now'), UINT_TO_DINT(GVL.EspArrUs));
        EcTrStr := '';
        EcEvtI := EcEvtFrom;
        WHILE (EcEvtI < GVL.ArrLogN) AND (EcEvtI < EcEvtFrom + 12) DO
            EcTrStr := CONCAT(EcTrStr, UDINT_TO_STRING(GVL.ArrLogMs[EcEvtI MOD 4096]));
            EcTrStr := CONCAT(EcTrStr, ':');
            EcTrStr := CONCAT(EcTrStr, UINT_TO_STRING(GVL.ArrLogMin[EcEvtI MOD 4096]));
            EcTrStr := CONCAT(EcTrStr, ':');
            EcTrStr := CONCAT(EcTrStr, UINT_TO_STRING(GVL.ArrLogMax[EcEvtI MOD 4096]));
            EcTrStr := CONCAT(EcTrStr, ',');
            EcEvtI := EcEvtI + 1;
        END_WHILE;
        MessagePackerFb.PackKvStr(ADR('ev'), ADR(EcTrStr));
    ELSIF (CommandType = 'DEM_EVT') THEN
        // The delay-change event log of drive axis:k (GVL.DemEvt):
        // n = events so far, ev = up to 20 entries from index from:i
        // (ms * 4 + code, comma separated). Entries older than
        // n - 1024 are overwritten.
        ShouldReturn := TRUE;
        CommandAck := TRUE;
        ResponsePacketPointer^.ele_count := 0;
        EcTrI := LIMIT(0, LINT_TO_INT(MessageUnpackerFb.TryReadINT64('axis', 0)), 2);
        EcEvtFrom := LINT_TO_UDINT(MessageUnpackerFb.TryReadINT64('from', 0));
        MessagePackerFb.PackKvLint(ADR('n'), UDINT_TO_LINT(GVL.DemEvtN[EcTrI]));
        EcTrStr := '';
        EcEvtI := EcEvtFrom;
        WHILE (EcEvtI < GVL.DemEvtN[EcTrI]) AND (EcEvtI < EcEvtFrom + 20) DO
            EcTrStr := CONCAT(EcTrStr, UDINT_TO_STRING(GVL.DemEvt[EcTrI, EcEvtI MOD 1024]));
            EcTrStr := CONCAT(EcTrStr, ',');
            EcEvtI := EcEvtI + 1;
        END_WHILE;
        MessagePackerFb.PackKvStr(ADR('ev'), ADR(EcTrStr));
    ELSIF (CommandType = 'DEM_STATS') THEN
        // The drives' position demand (0x6062) vs the targets we sent
        // (GVL.Dem*, PRG_EventLog). Per drive k: ds{k} stale, dlate{k}
        // one cycle behind the nominal lag, dgl{k} / dmx{k} d2 glitches /
        // max, dlt{k} PLC ms of the last stale, dl{k} the lag histogram
        // as text (lag 0..6, none). nsnap = snapshots taken; snap:i adds
        // snapshot i (s_axis, s_ms, s_why, s_t0 / s_t1 and s_d0 / s_d1 =
        // cycles 0-15 / 16-31 of target / demand). reset:1 clears.
        ShouldReturn := TRUE;
        CommandAck := TRUE;
        ResponsePacketPointer^.ele_count := 0;
        MessagePackerFb.PackKvDint(ADR('lagnom'), INT_TO_DINT(GVL.DemLagNominal));
        FOR EcTrI := 0 TO 2 DO
            EcObjKey := CONCAT('ds', INT_TO_STRING(EcTrI));
            MessagePackerFb.PackKvLint(ADR(EcObjKey), UDINT_TO_LINT(GVL.DemStale[EcTrI]));
            EcObjKey := CONCAT('dlate', INT_TO_STRING(EcTrI));
            MessagePackerFb.PackKvLint(ADR(EcObjKey), UDINT_TO_LINT(GVL.DemLate[EcTrI]));
            EcObjKey := CONCAT('dgl', INT_TO_STRING(EcTrI));
            MessagePackerFb.PackKvLint(ADR(EcObjKey), UDINT_TO_LINT(GVL.DemGlitch[EcTrI]));
            EcObjKey := CONCAT('dmx', INT_TO_STRING(EcTrI));
            MessagePackerFb.PackKvDint(ADR(EcObjKey), GVL.DemD2Max[EcTrI]);
            EcObjKey := CONCAT('dlt', INT_TO_STRING(EcTrI));
            MessagePackerFb.PackKvLint(ADR(EcObjKey), UDINT_TO_LINT(GVL.DemLastStaleMs[EcTrI]));
            EcTrStr := '';
            FOR EcObjJ := 0 TO 7 DO
                EcTrStr := CONCAT(EcTrStr, UDINT_TO_STRING(GVL.DemLag[EcTrI, EcObjJ]));
                EcTrStr := CONCAT(EcTrStr, ',');
            END_FOR;
            EcObjKey := CONCAT('dl', INT_TO_STRING(EcTrI));
            MessagePackerFb.PackKvStr(ADR(EcObjKey), ADR(EcTrStr));
        END_FOR;
        FOR EcTrI := 0 TO 2 DO
            EcObjKey := CONCAT('soff', INT_TO_STRING(EcTrI));
            MessagePackerFb.PackKvLint(ADR(EcObjKey), UDINT_TO_LINT(GVL.SyncOffCount[EcTrI]));
            EcObjKey := CONCAT('sofl', INT_TO_STRING(EcTrI));
            MessagePackerFb.PackKvLint(ADR(EcObjKey), UDINT_TO_LINT(GVL.SyncOffAndLate[EcTrI]));
            EcObjKey := CONCAT('stat', INT_TO_STRING(EcTrI));
            CASE EcTrI OF
                0: MessagePackerFb.PackKvDint(ADR(EcObjKey), WORD_TO_DINT(GVL.StatusTap0));
                1: MessagePackerFb.PackKvDint(ADR(EcObjKey), WORD_TO_DINT(GVL.StatusTap1));
            ELSE
                MessagePackerFb.PackKvDint(ADR(EcObjKey), WORD_TO_DINT(GVL.StatusTap2));
            END_CASE;
        END_FOR;
        MessagePackerFb.PackKvDint(ADR('nsnap'), INT_TO_DINT(GVL.DemSnapN));
        EcTrI := LINT_TO_INT(MessageUnpackerFb.TryReadINT64('snap', -1));
        IF (EcTrI >= 0) AND (EcTrI < GVL.DemSnapN) THEN
            MessagePackerFb.PackKvDint(ADR('s_axis'), INT_TO_DINT(GVL.DemSnapAxis[EcTrI]));
            MessagePackerFb.PackKvLint(ADR('s_ms'), UDINT_TO_LINT(GVL.DemSnapMs[EcTrI]));
            MessagePackerFb.PackKvDint(ADR('s_why'), INT_TO_DINT(GVL.DemSnapWhy[EcTrI]));
            EcTrStr := '';
            FOR EcSnapK := 0 TO 31 DO
                IF (GVL.DemSnapS[EcTrI, EcSnapK] AND 16#4000) <> 0 THEN
                    EcTrStr := CONCAT(EcTrStr, '1');
                ELSE
                    EcTrStr := CONCAT(EcTrStr, '0');
                END_IF;
            END_FOR;
            MessagePackerFb.PackKvStr(ADR('s_sync'), ADR(EcTrStr));
            FOR EcObjJ := 0 TO 3 DO
                EcTrStr := '';
                FOR EcSnapK := 0 TO 15 DO
                    IF EcObjJ < 2 THEN
                        EcTrStr := CONCAT(EcTrStr, DINT_TO_STRING(GVL.DemSnapT[EcTrI, (EcObjJ MOD 2) * 16 + EcSnapK]));
                    ELSE
                        EcTrStr := CONCAT(EcTrStr, DINT_TO_STRING(GVL.DemSnapD[EcTrI, (EcObjJ MOD 2) * 16 + EcSnapK]));
                    END_IF;
                    EcTrStr := CONCAT(EcTrStr, ',');
                END_FOR;
                CASE EcObjJ OF
                    0: EcObjKey := 's_t0';
                    1: EcObjKey := 's_t1';
                    2: EcObjKey := 's_d0';
                ELSE
                    EcObjKey := 's_d1';
                END_CASE;
                MessagePackerFb.PackKvStr(ADR(EcObjKey), ADR(EcTrStr));
            END_FOR;
        END_IF;
        IF MessageUnpackerFb.TryReadINT64('reset', 0) <> 0 THEN
            GVL.DemReset := TRUE;
        END_IF;
    ELSIF (CommandType = 'EC_STATS') THEN
        // EtherCAT health: the master's frame statistics (lost /
        // tx_err / rx_err, updated every 100 cycles) and the task
        // timing monitor (GVL.EcProf*: cycles, late100 / late500 =
        // periods over nominal + 100 / 500 us, pmin / pmax us,
        // h0..h5 = |period - nominal| < 20/50/100/200/500/>=500 us,
        // dc_out = cycles with DC out of sync, agsm_max us). reset:1
        // starts all of them over after this reply.
        ShouldReturn := TRUE;
        CommandAck := TRUE;
        ResponsePacketPointer^.ele_count := 0;
        MessagePackerFb.PackKvLint(ADR('lost'), UDINT_TO_LINT(GVL.EcLostFrames));
        MessagePackerFb.PackKvLint(ADR('tx_err'), UDINT_TO_LINT(GVL.EcTxErrors));
        MessagePackerFb.PackKvLint(ADR('rx_err'), UDINT_TO_LINT(GVL.EcRxErrors));
        MessagePackerFb.PackKvLint(ADR('cycles'), UDINT_TO_LINT(GVL.EcProfCycles));
        MessagePackerFb.PackKvLint(ADR('late100'), UDINT_TO_LINT(GVL.EcLate100));
        MessagePackerFb.PackKvLint(ADR('late500'), UDINT_TO_LINT(GVL.EcLate500));
        MessagePackerFb.PackKvReal(ADR('pmin'), LREAL_TO_REAL(GVL.EcPeriodUsMin));
        MessagePackerFb.PackKvReal(ADR('pmax'), LREAL_TO_REAL(GVL.EcPeriodUsMax));
        MessagePackerFb.PackKvLint(ADR('h0'), UDINT_TO_LINT(GVL.EcPeriodHist[0]));
        MessagePackerFb.PackKvLint(ADR('h1'), UDINT_TO_LINT(GVL.EcPeriodHist[1]));
        MessagePackerFb.PackKvLint(ADR('h2'), UDINT_TO_LINT(GVL.EcPeriodHist[2]));
        MessagePackerFb.PackKvLint(ADR('h3'), UDINT_TO_LINT(GVL.EcPeriodHist[3]));
        MessagePackerFb.PackKvLint(ADR('h4'), UDINT_TO_LINT(GVL.EcPeriodHist[4]));
        MessagePackerFb.PackKvLint(ADR('h5'), UDINT_TO_LINT(GVL.EcPeriodHist[5]));
        MessagePackerFb.PackKvLint(ADR('dc_out'), UDINT_TO_LINT(GVL.EcDcOutCycles));
        MessagePackerFb.PackKvReal(ADR('agsm_max'), LREAL_TO_REAL(GVL.EcAgsmUsMax));
        // g0..2 / gms0..2 / d2m0..2: set-position glitches of the
        // delta joints (count, PLC ms of the last, max |d2| deg/cycle^2)
        MessagePackerFb.PackKvLint(ADR('g0'), UDINT_TO_LINT(GVL.SetGlitchCount[0]));
        MessagePackerFb.PackKvLint(ADR('g1'), UDINT_TO_LINT(GVL.SetGlitchCount[1]));
        MessagePackerFb.PackKvLint(ADR('g2'), UDINT_TO_LINT(GVL.SetGlitchCount[2]));
        MessagePackerFb.PackKvLint(ADR('gms0'), UDINT_TO_LINT(GVL.SetGlitchLastMs[0]));
        MessagePackerFb.PackKvLint(ADR('gms1'), UDINT_TO_LINT(GVL.SetGlitchLastMs[1]));
        MessagePackerFb.PackKvLint(ADR('gms2'), UDINT_TO_LINT(GVL.SetGlitchLastMs[2]));
        MessagePackerFb.PackKvReal(ADR('d2m0'), LREAL_TO_REAL(GVL.SetD2Max[0]));
        MessagePackerFb.PackKvReal(ADR('d2m1'), LREAL_TO_REAL(GVL.SetD2Max[1]));
        MessagePackerFb.PackKvReal(ADR('d2m2'), LREAL_TO_REAL(GVL.SetD2Max[2]));
        // tic_max / tic0..9: where the task's IEC code ends in the
        // DC cycle (us, 100 us bins); sm0..2 / se0..2: the drives'
        // 0x1C32:0B SM-event missed / :20 sync error (-1 = SDO
        // failed, sdo_err)
        MessagePackerFb.PackKvReal(ADR('tic_max'), LREAL_TO_REAL(GVL.EcTicUsMax));
        MessagePackerFb.PackKvLint(ADR('tic0'), UDINT_TO_LINT(GVL.EcTicHist[0]));
        MessagePackerFb.PackKvLint(ADR('tic1'), UDINT_TO_LINT(GVL.EcTicHist[1]));
        MessagePackerFb.PackKvLint(ADR('tic2'), UDINT_TO_LINT(GVL.EcTicHist[2]));
        MessagePackerFb.PackKvLint(ADR('tic3'), UDINT_TO_LINT(GVL.EcTicHist[3]));
        MessagePackerFb.PackKvLint(ADR('tic4'), UDINT_TO_LINT(GVL.EcTicHist[4]));
        MessagePackerFb.PackKvLint(ADR('tic5'), UDINT_TO_LINT(GVL.EcTicHist[5]));
        MessagePackerFb.PackKvLint(ADR('tic6'), UDINT_TO_LINT(GVL.EcTicHist[6]));
        MessagePackerFb.PackKvLint(ADR('tic7'), UDINT_TO_LINT(GVL.EcTicHist[7]));
        MessagePackerFb.PackKvLint(ADR('tic8'), UDINT_TO_LINT(GVL.EcTicHist[8]));
        MessagePackerFb.PackKvLint(ADR('tic9'), UDINT_TO_LINT(GVL.EcTicHist[9]));
        MessagePackerFb.PackKvDint(ADR('sm0'), GVL.DrvSmMissed[0]);
        MessagePackerFb.PackKvDint(ADR('sm1'), GVL.DrvSmMissed[1]);
        MessagePackerFb.PackKvDint(ADR('sm2'), GVL.DrvSmMissed[2]);
        MessagePackerFb.PackKvDint(ADR('se0'), GVL.DrvSyncErr[0]);
        MessagePackerFb.PackKvDint(ADR('se1'), GVL.DrvSyncErr[1]);
        MessagePackerFb.PackKvDint(ADR('se2'), GVL.DrvSyncErr[2]);
        MessagePackerFb.PackKvDint(ADR('sdo_err'), GVL.EcSdoErr);
        MessagePackerFb.PackKvLint(ADR('bus_us'), DWORD_TO_LINT(AxisGroupSM.BusPeriodUs));   // EtherCAT_Task interval as read
        // Comm task profile: cs0..2 worst segment us (TCP server / MbProbe / EspHttp),
        // cb0..2 bus cycles inside it, co0..2 segments > 1 ms, cscan worst whole scan
        MessagePackerFb.PackKvReal(ADR('cs0'), LREAL_TO_REAL(GVL.CommSegUsMax[0]));
        MessagePackerFb.PackKvLint(ADR('cb0'), UDINT_TO_LINT(GVL.CommSegBusCyc[0]));
        MessagePackerFb.PackKvLint(ADR('co0'), UDINT_TO_LINT(GVL.CommSegOver1ms[0]));
        MessagePackerFb.PackKvReal(ADR('cs1'), LREAL_TO_REAL(GVL.CommSegUsMax[1]));
        MessagePackerFb.PackKvLint(ADR('cb1'), UDINT_TO_LINT(GVL.CommSegBusCyc[1]));
        MessagePackerFb.PackKvLint(ADR('co1'), UDINT_TO_LINT(GVL.CommSegOver1ms[1]));
        MessagePackerFb.PackKvReal(ADR('cs2'), LREAL_TO_REAL(GVL.CommSegUsMax[2]));
        MessagePackerFb.PackKvLint(ADR('cb2'), UDINT_TO_LINT(GVL.CommSegBusCyc[2]));
        MessagePackerFb.PackKvLint(ADR('co2'), UDINT_TO_LINT(GVL.CommSegOver1ms[2]));
        MessagePackerFb.PackKvReal(ADR('cscan'), LREAL_TO_REAL(GVL.CommScanUsMax));
        // fj0..3 / fm0..3 / fl0..3: following-error jumps (EAxis0..2, reel): count, max, PLC ms of the last
        MessagePackerFb.PackKvLint(ADR('fj0'), UDINT_TO_LINT(GVL.FeJumpCount[0]));
        MessagePackerFb.PackKvReal(ADR('fm0'), LREAL_TO_REAL(GVL.FeJumpMax[0]));
        MessagePackerFb.PackKvLint(ADR('fl0'), UDINT_TO_LINT(GVL.FeLastJumpMs[0]));
        MessagePackerFb.PackKvLint(ADR('fj1'), UDINT_TO_LINT(GVL.FeJumpCount[1]));
        MessagePackerFb.PackKvReal(ADR('fm1'), LREAL_TO_REAL(GVL.FeJumpMax[1]));
        MessagePackerFb.PackKvLint(ADR('fl1'), UDINT_TO_LINT(GVL.FeLastJumpMs[1]));
        MessagePackerFb.PackKvLint(ADR('fj2'), UDINT_TO_LINT(GVL.FeJumpCount[2]));
        MessagePackerFb.PackKvReal(ADR('fm2'), LREAL_TO_REAL(GVL.FeJumpMax[2]));
        MessagePackerFb.PackKvLint(ADR('fl2'), UDINT_TO_LINT(GVL.FeLastJumpMs[2]));
        MessagePackerFb.PackKvLint(ADR('fj3'), UDINT_TO_LINT(GVL.FeJumpCount[3]));
        MessagePackerFb.PackKvReal(ADR('fm3'), LREAL_TO_REAL(GVL.FeJumpMax[3]));
        MessagePackerFb.PackKvLint(ADR('fl3'), UDINT_TO_LINT(GVL.FeLastJumpMs[3]));
        MessagePackerFb.PackKvReal(ADR('ctask'), LREAL_TO_REAL(GVL.CommTaskUsMax));
        MessagePackerFb.PackKvReal(ADR('cout'), LREAL_TO_REAL(GVL.CommOutsideUsMax));
        MessagePackerFb.PackKvLint(ADR('cout500'), UDINT_TO_LINT(GVL.CommOutsideOver500));
        IF MessageUnpackerFb.TryReadINT64('obj', 0) <> 0 THEN  // long text: only on request
            // o0..o2: drive k's sync objects (DrvObj), "v,v,..." text
            FOR EcTrI := 0 TO 2 DO
                EcTrStr := '';
                FOR EcObjJ := 0 TO 11 DO
                    EcTrStr := CONCAT(EcTrStr, DINT_TO_STRING(GVL.DrvObj[EcTrI, EcObjJ]));
                    EcTrStr := CONCAT(EcTrStr, ',');
                END_FOR;
                EcObjKey := CONCAT('o', INT_TO_STRING(EcTrI));
                MessagePackerFb.PackKvStr(ADR(EcObjKey), ADR(EcTrStr));
            END_FOR;
        END_IF;
        // dg0..2 / dd0..2: the same glitch check on the integer
        // target sent to the drive (diSetPosition): count, max |d2|
        MessagePackerFb.PackKvLint(ADR('dg0'), UDINT_TO_LINT(GVL.DiGlitchCount[0]));
        MessagePackerFb.PackKvLint(ADR('dg1'), UDINT_TO_LINT(GVL.DiGlitchCount[1]));
        MessagePackerFb.PackKvLint(ADR('dg2'), UDINT_TO_LINT(GVL.DiGlitchCount[2]));
        MessagePackerFb.PackKvDint(ADR('dd0'), GVL.DiD2Max[0]);
        MessagePackerFb.PackKvDint(ADR('dd1'), GVL.DiD2Max[1]);
        MessagePackerFb.PackKvDint(ADR('dd2'), GVL.DiD2Max[2]);
        // tap*: the Target Position in the output image (read-only AT taps)
        MessagePackerFb.PackKvLint(ADR('tg0'), UDINT_TO_LINT(GVL.TapGlitch[0]));
        MessagePackerFb.PackKvDint(ADR('td0'), GVL.TapD2Max[0]);
        MessagePackerFb.PackKvLint(ADR('tnow0'), UDINT_TO_LINT(GVL.TapEqNow[0]));
        MessagePackerFb.PackKvLint(ADR('tprev0'), UDINT_TO_LINT(GVL.TapEqPrev[0]));
        MessagePackerFb.PackKvLint(ADR('tnone0'), UDINT_TO_LINT(GVL.TapEqNone[0]));
        MessagePackerFb.PackKvLint(ADR('tg1'), UDINT_TO_LINT(GVL.TapGlitch[1]));
        MessagePackerFb.PackKvDint(ADR('td1'), GVL.TapD2Max[1]);
        MessagePackerFb.PackKvLint(ADR('tnow1'), UDINT_TO_LINT(GVL.TapEqNow[1]));
        MessagePackerFb.PackKvLint(ADR('tprev1'), UDINT_TO_LINT(GVL.TapEqPrev[1]));
        MessagePackerFb.PackKvLint(ADR('tnone1'), UDINT_TO_LINT(GVL.TapEqNone[1]));
        MessagePackerFb.PackKvLint(ADR('tg2'), UDINT_TO_LINT(GVL.TapGlitch[2]));
        MessagePackerFb.PackKvDint(ADR('td2'), GVL.TapD2Max[2]);
        MessagePackerFb.PackKvLint(ADR('tnow2'), UDINT_TO_LINT(GVL.TapEqNow[2]));
        MessagePackerFb.PackKvLint(ADR('tprev2'), UDINT_TO_LINT(GVL.TapEqPrev[2]));
        MessagePackerFb.PackKvLint(ADR('tnone2'), UDINT_TO_LINT(GVL.TapEqNone[2]));
        // EasyCAT bus timing probe: e_stale / e_skip since reset,
        // e_late last second, e_late_sum since reset (the arrival
        // min / max moved to the per-100-ms ArrLog, SYS ESP_ARR)
        MessagePackerFb.PackKvDint(ADR('e_stale'), UINT_TO_DINT(GVL.EspStale - GVL.EspStale0));
        MessagePackerFb.PackKvDint(ADR('e_skip'), UINT_TO_DINT(GVL.EspSkip - GVL.EspSkip0));
        MessagePackerFb.PackKvDint(ADR('e_late'), UINT_TO_DINT(GVL.EspLate));
        MessagePackerFb.PackKvLint(ADR('e_late_sum'), UDINT_TO_LINT(GVL.EspLateSum));
        // e_pg / e_pd2 / e_pd2peak: EAxis0's target stream checked on the ESP32
        MessagePackerFb.PackKvDint(ADR('e_pg'), UINT_TO_DINT(GVL.EspPosGlitch - GVL.EspPosGlitch0));
        MessagePackerFb.PackKvLint(ADR('e_pd2'), UDINT_TO_LINT(GVL.EspPosD2Max));
        MessagePackerFb.PackKvLint(ADR('e_pd2peak'), UDINT_TO_LINT(GVL.EspPosD2Peak));
        IF MessageUnpackerFb.TryReadINT64('tr', 0) <> 0 THEN   // long text: only on request
            // trl / trh: the last 20 x 500 ms min / max of the above,
            // oldest first ("lo0,..." as text, us)
            EcTrStr := '';
            FOR EcTrI := 0 TO 19 DO
                EcTrStr := CONCAT(EcTrStr, REAL_TO_STRING(GVL.EcTicTrMin[(GVL.EcTicIdx + EcTrI) MOD 20]));
                EcTrStr := CONCAT(EcTrStr, ',');
            END_FOR;
            MessagePackerFb.PackKvStr(ADR('trl'), ADR(EcTrStr));
            EcTrStr := '';
            FOR EcTrI := 0 TO 19 DO
                EcTrStr := CONCAT(EcTrStr, REAL_TO_STRING(GVL.EcTicTrMax[(GVL.EcTicIdx + EcTrI) MOD 20]));
                EcTrStr := CONCAT(EcTrStr, ',');
            END_FOR;
            MessagePackerFb.PackKvStr(ADR('trh'), ADR(EcTrStr));
        END_IF;
        IF MessageUnpackerFb.TryReadINT64('reset', 0) <> 0 THEN
            GVL.EcProfReset := TRUE;
            GVL.EcStatsClear := TRUE;
            GVL.CommProfReset := TRUE;
        END_IF;
    ELSIF (CommandType = 'TASK_STATS') THEN
        // The runtime's task statistics -- what the IDE's Task
        // Configuration > Monitor shows -- for every task of the
        // application, SoftMotion's planning task included. Flat
        // keys per task k: tk_name, tk_prio, tk_interval_us,
        // tk_last / tk_avg / tk_max / tk_min (cycle time, us),
        // tk_jit / tk_jmin / tk_jmax (jitter, us), tk_cycles,
        // tk_iec_cycles; n = tasks. reset:true zeroes them after
        // reading (as the IDE's reset does).
        ShouldReturn := TRUE;
        CommandAck := TRUE;
        ResponsePacketPointer^.ele_count := 0;
        TsReset := MessageUnpackerFb.TryReadINT64('reset', 0) <> 0;
        TsN := 0;
        TsH := CmpIecTask.IecTaskGetFirst(ADR('Application'), ADR(TsR));
        WHILE (TsR = 0) AND (TsN < 8) DO
            TsP := CmpIecTask.IecTaskGetInfo3(TsH, ADR(TsR));
            IF TsP <> 0 THEN
                IF TsP^.pszName <> 0 THEN
                    TsName := TsP^.pszName^;
                ELSE
                    TsName := '?';
                END_IF;
                TsKey := CONCAT(CONCAT('t', INT_TO_STRING(TsN)), '_name');
                MessagePackerFb.PackKvStr(ADR(TsKey), ADR(TsName));
                TsKey := CONCAT(CONCAT('t', INT_TO_STRING(TsN)), '_prio');
                MessagePackerFb.PackKvDint(ADR(TsKey), TsP^.nPriority);
                TsKey := CONCAT(CONCAT('t', INT_TO_STRING(TsN)), '_interval_us');
                MessagePackerFb.PackKvLint(ADR(TsKey), DWORD_TO_LINT(TsP^.dwInterval));
                TsKey := CONCAT(CONCAT('t', INT_TO_STRING(TsN)), '_last');
                MessagePackerFb.PackKvLint(ADR(TsKey), DWORD_TO_LINT(TsP^.dwCycleTime));
                TsKey := CONCAT(CONCAT('t', INT_TO_STRING(TsN)), '_avg');
                MessagePackerFb.PackKvLint(ADR(TsKey), DWORD_TO_LINT(TsP^.dwAverageCycleTime));
                TsKey := CONCAT(CONCAT('t', INT_TO_STRING(TsN)), '_max');
                MessagePackerFb.PackKvLint(ADR(TsKey), DWORD_TO_LINT(TsP^.dwMaxCycleTime));
                TsKey := CONCAT(CONCAT('t', INT_TO_STRING(TsN)), '_min');
                MessagePackerFb.PackKvLint(ADR(TsKey), DWORD_TO_LINT(TsP^.dwMinCycleTime));
                TsKey := CONCAT(CONCAT('t', INT_TO_STRING(TsN)), '_jit');
                MessagePackerFb.PackKvDint(ADR(TsKey), TsP^.iJitter);
                TsKey := CONCAT(CONCAT('t', INT_TO_STRING(TsN)), '_jmin');
                MessagePackerFb.PackKvDint(ADR(TsKey), TsP^.iJitterMin);
                TsKey := CONCAT(CONCAT('t', INT_TO_STRING(TsN)), '_jmax');
                MessagePackerFb.PackKvDint(ADR(TsKey), TsP^.iJitterMax);
                TsKey := CONCAT(CONCAT('t', INT_TO_STRING(TsN)), '_cycles');
                MessagePackerFb.PackKvLint(ADR(TsKey), DWORD_TO_LINT(TsP^.dwCycleCount));
                TsKey := CONCAT(CONCAT('t', INT_TO_STRING(TsN)), '_iec_cycles');
                MessagePackerFb.PackKvLint(ADR(TsKey), DWORD_TO_LINT(TsP^.dwIECCycleCount));
                TsN := TsN + 1;
            END_IF;
            IF TsReset THEN
                CmpIecTask.IecTaskResetStatistics(TsH);
            END_IF;
            TsH := CmpIecTask.IecTaskGetNext(ADR('Application'), TsH, ADR(TsR));
        END_WHILE;
        MessagePackerFb.PackKvDint(ADR('n'), TsN);
    ELSIF (CommandType = 'VERSION') THEN
        // Build provenance: lets an external tool verify the running
        // PLC matches a repo commit without IDE assistance. Stamped by
        // codesys_scripts/jobs/stamp_build_info.py before import_all.
        ShouldReturn := TRUE;
        CommandAck := TRUE;
        ResponsePacketPointer^.ele_count := 0;
        MessagePackerFb.PackKvStr(ADR('git_sha'), ADR(GVL.BUILD_GIT_SHA));
        MessagePackerFb.PackKvLint(ADR('build_ts_ms'), GVL.BUILD_TS_MS);
        MessagePackerFb.PackKvLint(ADR('runtime_ms'), AxisGroupSM.RuntimeMs);
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
        MessagePackerFb.PackKvLint(ADR('runtime_ms'), AxisGroupSM.RuntimeMs);
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
        MessagePackerFb.PackKvLint(ADR('comm_gap_max_ms'), GVL.CommGapMaxMs);
        MessagePackerFb.PackKvLint(ADR('comm_gap_max_at_ms'), GVL.CommGapMaxAtMs);
        MessagePackerFb.PackKvLint(ADR('comm_gaps_over_20ms'), UDINT_TO_LINT(GVL.CommGapsOver20));
        MessagePackerFb.PackKvLint(ADR('diag_send_drop'), UDINT_TO_LINT(GVL.DiagReplySendDrop));
        MessagePackerFb.PackKvLint(ADR('maint_arm_count'), UDINT_TO_LINT(GVL.MaintArmCount));
        MessagePackerFb.PackKvLint(ADR('maint_refused'), UDINT_TO_LINT(GVL.MaintRefusedCount));
        MessagePackerFb.PackKvLint(ADR('fault_seq'), UDINT_TO_LINT(GVL.FaultSeq));
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
        MessagePackerFb.PackKvDint(ADR('io_cmd_count'), TO_DINT(AxisGroupSM.IoCommandCount));
        MessagePackerFb.PackKvDint(ADR('io_trig_count'), TO_DINT(AxisGroupSM.IoTriggerCount));
        MessagePackerFb.PackKvDint(ADR('flyevent_avail'), TO_DINT(AxisGroupSM.FlyEventAvailableCount));
ELSE
    CommandAck := FALSE;
    ResponsePacketPointer^.ele_count := 0;
    MessagePackerFb.PackKvStr(ADR('err'), ADR('unknown SYS cmd'));
    UnknownCount := UnknownCount + 1;
END_IF;

MessagePackerFb.PackKvDint(ADR('id'), CommandId);
MessagePackerFb.PackKvBool(ADR('ack'), CommandAck);
IF MsgPakInfoWrapup(DiagInfo) THEN
    OutLen := DiagInfo.buf_size;
    HandledCount := HandledCount + 1;
END_IF;
ReqLen := 0;
