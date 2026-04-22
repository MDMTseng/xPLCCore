METHOD consumeTail : BOOL
VAR_INPUT
END_VAR
(* =========== IMPLEMENTATION =========== *)
    // Check for initialization and if there are items to consume
    IF (_count = 0) THEN
        consumeTail := FALSE; // Cannot consume, buffer is empty or not ready
        RETURN;
    END_IF;
    
    // Advance the tail pointer, wrapping around if necessary
    _tail := (_tail + 1) MOD _capacity;
    
    // Decrement the number of items in the buffer
    _count := _count - 1;
    
    consumeTail := TRUE;