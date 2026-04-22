(*
    METHOD write_length
    
    Increments the buffer's size by the specified length, effectively "writing" data to it.
    This should be called after an external write operation (e.g., NBS.Read).
*)
METHOD write_length
VAR_INPUT
     udiLength: DINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
IF pBuffer <> 0 THEN
    // Check for overflow against the logical capacity.
    IF (udiCurrentSize + udiLength) <= udiCapacity THEN
        udiCurrentSize := udiCurrentSize + udiLength;
    ELSE
        // Handle overflow: set size to max capacity.
        udiCurrentSize := udiCapacity;
    END_IF
END_IF