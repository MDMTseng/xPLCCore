METHOD FB_init : BOOL
VAR_INPUT
    bInitRetains : BOOL; // This input is required by the FB_init standard but not used here.
    bInCopyCode  : BOOL; // This input is required by the FB_init standard but not used here.
    ring_buffer_size : UINT; // The total capacity of the buffer
END_VAR
(* =========== IMPLEMENTATION =========== *)

// Inlined from the former init() method (had no external callers --
// only FB_init wrapped it). Removes the redundant indirection.

_head := 0;
_tail := 0;
_capacity := ring_buffer_size;
// Pointer arithmetic happens modulo _cap2 so empty (head==tail) is
// distinct from full (size==_capacity). UINT max=65535; safe for any
// ring_buffer_size up to 32767.
_cap2 := ring_buffer_size * 2;

IF ring_buffer_size = 0 THEN
    // Buffer capacity cannot be zero.
    FB_init := FALSE;
    RETURN;
END_IF;

FB_init := TRUE;
