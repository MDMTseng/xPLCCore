METHOD clear : BOOL
VAR_INPUT
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Writes both _head and _tail. Not safe under task preemption -- only
// call from a single-task context (init or offline). The legacy
// in-loop clear() in AxisGroupSM was removed when the not-Ready drain
// path switched to per-packet NAK.
_head := 0;
_tail := 0;
