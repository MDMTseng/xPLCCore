(*
    METHOD GetSpace : UDINT
    
    Returns the number of available bytes from the head to the end of the buffer.
*)
METHOD GetSpace : DINT
(* =========== IMPLEMENTATION =========== *)
IF pBuffer <> 0 THEN
    GetSpace := udiCapacity - udiCurrentSize;
ELSE
    GetSpace := 0;
END_IF