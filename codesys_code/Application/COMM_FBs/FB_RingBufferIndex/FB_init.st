METHOD FB_init : BOOL
VAR_INPUT
    bInitRetains : BOOL; // This input is required by the FB_init standard but not used here.
    bInCopyCode  : BOOL; // This input is required by the FB_init standard but not used here.
    ring_buffer_size : UINT; // The total capacity of the buffer
END_VAR
(* =========== IMPLEMENTATION =========== *)



FB_init:=init(ring_buffer_size);