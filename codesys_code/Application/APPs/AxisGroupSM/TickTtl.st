METHOD TickTtl : BOOL
VAR_INPUT
    p : POINTER TO FlyEventData;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Common TTL-decrement-and-expire logic factored out of
// ProcessFlyEventsAndIo. Each of DistanceTrigger / MovmentProgress /
// PulseTrigger ran the same 7 lines verbatim in their ELSE branch.
//
// Returns TRUE when the TTL just expired (caller should set
// TriggerErrorCode = TRIGGER_TIMEOUT_ERR and run the trigger-fired
// path with the error). TimeToLiveMs = -1 means "no expiry"; only
// strictly positive values decay.
TickTtl := FALSE;
IF (p^.TimeToLiveMs > 0) THEN
    p^.TimeToLiveMs := p^.TimeToLiveMs - BusPeriod;
    IF (p^.TimeToLiveMs <= 0) THEN
        TickTtl := TRUE;
    END_IF;
END_IF;
