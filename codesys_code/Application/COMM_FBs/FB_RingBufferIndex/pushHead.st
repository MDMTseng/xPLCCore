METHOD pushHead : BOOL
VAR_INPUT
END_VAR
(* =========== IMPLEMENTATION =========== *)

    // Check for initialization and if there is space
    IF (_count >= _capacity) THEN
        pushHead := FALSE; // Cannot push, buffer is full or not ready
        RETURN;
    END_IF;

    // Advance the head pointer, wrapping around if necessary
    _head := (_head + 1) MOD _capacity;
    
    // Increment the number of items in the buffer
    _count := _count + 1;
    
    pushHead := TRUE;