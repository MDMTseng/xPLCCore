METHOD init : BOOL
VAR_INPUT
    ring_buffer_size : UINT; // The total capacity of the buffer
END_VAR
(* =========== IMPLEMENTATION =========== *)
    

_head := 0;
_tail := 0;
_count := 0;
_capacity := ring_buffer_size;

IF ring_buffer_size = 0 THEN
	// Buffer capacity cannot be zero.
	init := FALSE;
	RETURN;
END_IF;

init := TRUE;