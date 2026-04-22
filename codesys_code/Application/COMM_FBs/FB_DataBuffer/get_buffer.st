METHOD get_buffer : POINTER TO BYTE
(* =========== IMPLEMENTATION =========== *)
IF pBuffer <> 0 THEN
    get_buffer := pBuffer;
ELSE
    // If the buffer has not been reset, return a NULL pointer.
    get_buffer := 0;
END_IF