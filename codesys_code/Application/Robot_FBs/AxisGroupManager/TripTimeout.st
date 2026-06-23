METHOD TripTimeout : BOOL
VAR_INPUT
    psStateName : POINTER TO STRING;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Latch a "<state>:timeout" error source + drive EV_ERROR. Used by
// Powering / GroupEnabling / Homing when their state-entry watchdog
// TON .Q latches. ErrorID is zero (no underlying FB code) -- the
// source string distinguishes the kind of timeout.
GVL.LastErrorSource := CONCAT(psStateName^, ':timeout');
GVL.LastErrorID := 0;
Transition(E_RobotEvent.EV_ERROR);
TripTimeout := TRUE;
