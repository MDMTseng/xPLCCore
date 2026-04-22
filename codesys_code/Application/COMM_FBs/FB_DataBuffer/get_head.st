METHOD get_head : POINTER TO BYTE
(* =========== IMPLEMENTATION =========== *)
IF pBuffer <> 0 THEN
    get_head := pBuffer + udiCurrentSize;
ELSE
    // If the buffer has not been reset, return a NULL pointer.
    get_head := 0;
END_IF