METHOD ResetDiagCounters : BOOL
(* =========== IMPLEMENTATION =========== *)
// R8 fix (test_plc_refactor_findings): the SYS and legacy-M
// RESET_DBG_INFO handlers each carried their own hand-maintained reset
// list and had already drifted (the M copy missed 4 counters, the SYS
// copy missed the Io* locals, and NEITHER cleared the newer counters:
// self_reentry, dupe_cmd, remp_drop_reply/_trig, pending_*_drop,
// remp_overflow_drop, io_*_count, ui_ping_count). A rebaseline through
// one path left residue the other would have cleared, and fuzz/soak
// baselines read that residue as regressions.
//
// This method is the ONE canonical list. Contract: everything GET_DIAG
// publishes as a counter gets cleared here -- when you add a counter to
// GET_DIAG, add it here in the same commit (the R8 acceptance test
// cross-checks the published set against zero after reset). Deliberately
// NOT cleared: RuntimeMs / AxisGroupSMScans (monotonic clocks),
// LastUiPingMs (live state), flyevent_avail (live capacity),
// server_active / bind_addr (live status).
ResetDiagCounters := TRUE;

// Drop / overflow counters.
GVL.ReMpDropCount                := 0;
GVL.ReMpDropCount_Reply          := 0;
GVL.ReMpDropCount_TriggerEvent   := 0;
GVL.ReMpOverflowDropCount        := 0;
GVL.OverlenDropCount             := 0;
GVL.SendStallDropCount           := 0;
GVL.PendingStChgDropCount        := 0;
GVL.PendingMoveDoneDropCount     := 0;

// NAK counters.
GVL.GroupNotReadyNakCount        := 0;
GVL.MissingTypeFieldNakCount     := 0;
GVL.CoordNotConfiguredNakCount   := 0;
GVL.ProtocolVersionMismatchCount := 0;
GVL.UnknownEventNakCount         := 0;
GVL.UnknownCmdNakCount           := 0;
GVL.ScratchpadPartialNakCount    := 0;
GVL.FlyEventRejectNakCount       := 0;
GVL.FlyEventBufferFullNakCount   := 0;
GVL.SetCoordBusyNakCount         := 0;
GVL.Coord1BadFrameNakCount       := 0;
GVL.Coord1NotBoundNakCount       := 0;
GVL.Coord1BindBusyNakCount       := 0;
GVL.Coord1ConflictNakCount       := 0;
GVL.Coord1BadTableNakCount       := 0;

// Transport / lifecycle counters.
GVL.IdleResetCount               := 0;
GVL.ReadErrorResetCount          := 0;
GVL.ParserErrorResetCount        := 0;
GVL.WriteErrorResetCount         := 0;
GVL.TxStallResetCount            := 0;
GVL.ClientConnectCount           := 0;
GVL.ServerLongIdleCount          := 0;

// Supervisor / FSM counters.
GVL.UiHeartbeatStaleCount        := 0;
GVL.GroupErrorStopTripCount      := 0;
GVL.StateChangeEventCount        := 0;
GVL.SelfReentryCount             := 0;

// Dedupe / IO counters (Io* live in AxisGroupSM PROGRAM scope).
GVL.DupeCommandCount             := 0;
IoCommandCount                   := 0;
IoTriggerCount                   := 0;
IoTriggerResetCount              := 0;
IoTriggerInfoCount               := 0;

// PING stats.
GVL.UiPingCount                  := 0;
GVL.PingMaxGapMs                 := 0;
GVL.PrevPingRuntimeMs            := 0;
