METHOD Size : UINT
VAR
    h : UINT;
    t : UINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Snapshot _head and _tail once. Each is single-task-owned and a UINT
// read is atomic on the CPU, so a stale cross-task read is at worst
// slightly conservative (a producer underestimates space for one scan,
// a consumer underestimates queued items for one scan). Both
// self-correct on the next call.
h := _head;
t := _tail;
IF h >= t THEN
    Size := h - t;
ELSE
    Size := h + _cap2 - t;
END_IF;
