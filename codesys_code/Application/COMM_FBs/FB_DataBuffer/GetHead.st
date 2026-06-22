METHOD GetHead : POINTER TO BYTE
(* =========== IMPLEMENTATION =========== *)
IF pBuffer <> 0 THEN
    GetHead := pBuffer + udiCurrentSize;
ELSE
    // If the buffer has not been reset, return a NULL pointer.
    GetHead := 0;
END_IF