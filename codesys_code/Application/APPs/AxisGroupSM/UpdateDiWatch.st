METHOD UpdateDiWatch : BOOL
VAR
    i       : INT;
    bitNow  : BOOL;
    bitPrev : BOOL;
    sent    : INT;
END_VAR
VAR CONSTANT
    DI_MS_MAX : DINT := 2000000000;   // ~23 days: the ms counts stop here
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Digital-input change events (SYS DI_WATCH): the host registers pins, an
// edge filter and a throttle; every scan (1 ms) this tracks each watched
// pin and pushes {kind:'event', name:'DI'} when it changes. The first
// matching edge after a quiet throttle period goes out at once; changes
// inside the throttle window are summarised into one event at its end,
// so nothing is lost to the throttle:
//   state        level now
//   flips        changes since the last event (any edge)
//   high_ms      time high since the last event
//   max_high_ms / max_low_ms   longest run at each level that ended since the
//                last event, or is still going (a run is not cut by an event)
//   t_first      RuntimeMs of the first change since the last event
//   reel_odo_um  reel odometer (GVL.ReelOdoMm, um) at that first change
//   t            RuntimeMs now
// A full reply ring defers the push to a later scan (at most 2 per scan,
// and never into the last 2 slots, which stay for command replies).
// Generic on purpose: what an input means is the host's business.
IF NOT DiWatchInit THEN
    DiWatchPrev := DigitalInputBits_Curr;
    DiWatchInit := TRUE;
END_IF;
IF NOT TCP_MSGPAK_Server.fbMyServer.CLIENT.xActive THEN
    // host gone: nobody to tell; it registers again when it reconnects
    DiWatchMask := 0;
END_IF;
IF DiWatchMask = 0 THEN
    DiWatchPrev := DigitalInputBits_Curr;
    RETURN;
END_IF;
sent := 0;
FOR i := 0 TO 15 DO
    IF (SHR(DiWatchMask, i) AND 1) <> 0 THEN
        bitNow  := (SHR(DigitalInputBits_Curr, i) AND 1) <> 0;
        bitPrev := (SHR(DiWatchPrev, i) AND 1) <> 0;
        IF bitNow <> bitPrev THEN
            // close the run of the level just left
            IF bitPrev THEN
                DiWMaxHighMs[i] := MAX(DiWMaxHighMs[i], DiWRunMs[i]);
            ELSE
                DiWMaxLowMs[i] := MAX(DiWMaxLowMs[i], DiWRunMs[i]);
            END_IF;
            DiWRunMs[i] := 0;
            IF DiWFlips[i] = 0 THEN
                DiWFirstMs[i] := RuntimeMs;
                DiWReelOdo[i] := GVL.ReelOdoMm;
            END_IF;
            DiWFlips[i] := DiWFlips[i] + 1;
            IF (bitNow AND ((SHR(DiWatchRise, i) AND 1) <> 0))
               OR (NOT bitNow AND ((SHR(DiWatchFall, i) AND 1) <> 0)) THEN
                DiWPending[i] := TRUE;
            END_IF;
        END_IF;
        // Saturating: a pin that does not change for 24.8 days would
        // otherwise wrap these DINT ms counts negative.
        IF DiWRunMs[i] < DI_MS_MAX THEN
            DiWRunMs[i] := DiWRunMs[i] + TO_DINT(BusPeriod);
        END_IF;
        IF bitNow AND (DiWHighMs[i] < DI_MS_MAX) THEN
            DiWHighMs[i] := DiWHighMs[i] + TO_DINT(BusPeriod);
        END_IF;
        IF DiWPending[i] AND (sent < 2)
           AND ((RuntimeMs - DiWLastSentMs[i]) >= DiWatchThrottleMs[i])
           AND (GVL.reMP_info_ridx.Space() > 2) THEN
            IF TryAcquireReplySlot(0) THEN
                MessagePackerFb.PackKvStr(ADR('kind'), ADR('event'));
                MessagePackerFb.PackKvStr(ADR('name'), ADR('DI'));
                MessagePackerFb.PackKvDint(ADR('pin'), i);
                MessagePackerFb.PackKvBool(ADR('state'), bitNow);
                MessagePackerFb.PackKvLint(ADR('flips'), UDINT_TO_LINT(DiWFlips[i]));
                MessagePackerFb.PackKvDint(ADR('high_ms'), DiWHighMs[i]);
                IF bitNow THEN
                    MessagePackerFb.PackKvDint(ADR('max_high_ms'), MAX(DiWMaxHighMs[i], DiWRunMs[i]));
                    MessagePackerFb.PackKvDint(ADR('max_low_ms'), DiWMaxLowMs[i]);
                ELSE
                    MessagePackerFb.PackKvDint(ADR('max_high_ms'), DiWMaxHighMs[i]);
                    MessagePackerFb.PackKvDint(ADR('max_low_ms'), MAX(DiWMaxLowMs[i], DiWRunMs[i]));
                END_IF;
                MessagePackerFb.PackKvLint(ADR('t_first'), DiWFirstMs[i]);
                MessagePackerFb.PackKvLint(ADR('reel_odo_um'), LREAL_TO_LINT(DiWReelOdo[i] * 1000.0));
                MessagePackerFb.PackKvLint(ADR('t'), RuntimeMs);
                CommitReplySlotEvent();
                DiWFlips[i] := 0;
                DiWHighMs[i] := 0;
                DiWMaxHighMs[i] := 0;
                DiWMaxLowMs[i] := 0;
                DiWPending[i] := FALSE;
                DiWLastSentMs[i] := RuntimeMs;
                GVL.DiEventCount := GVL.DiEventCount + 1;
                sent := sent + 1;
            END_IF;
        END_IF;
    END_IF;
END_FOR;
DiWatchPrev := DigitalInputBits_Curr;
