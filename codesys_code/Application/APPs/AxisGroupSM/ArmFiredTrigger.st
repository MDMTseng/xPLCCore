METHOD ArmFiredTrigger : BOOL
VAR_INPUT
    p : POINTER TO FlyEventData;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Common arm-after-fire logic factored out of ProcessFlyEventsAndIo.
// Each of DistanceTrigger / MovmentProgressTrigger / PulseTrigger
// hit-detection ran the same 8 lines verbatim; this method holds the
// canonical version.
//
// Returns the new TriggerActive value:
//   TRUE  -> action should run THIS scan (no IO stages OR first stage
//            has zero delay)
//   FALSE -> action deferred to SequenceDelayWait
//            (IoStageDelayCountdown set; trigger morphed to delay-wait)
ArmFiredTrigger := TRUE;
IF (p^.IoStageCount > 0) THEN
    p^.IoStageIndex := 0;
    p^.IoStageDelayCountdown := p^.IoStageDelay[0];
    p^.TriggerType := FlyEventTriggerType.SequenceDelayWait;
    IF (p^.IoStageDelayCountdown > 0) THEN
        ArmFiredTrigger := FALSE;
    END_IF;
END_IF;
