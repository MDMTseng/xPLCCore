METHOD GetBuffer : POINTER TO BYTE
(* =========== IMPLEMENTATION =========== *)
IF pBuffer <> 0 THEN
    GetBuffer := pBuffer;
ELSE
    // If the buffer has not been reset, return a NULL pointer.
    GetBuffer := 0;
END_IF