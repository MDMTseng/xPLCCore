(*
    METHOD get_space : UDINT
    
    Returns the number of available bytes from the head to the end of the buffer.
*)
METHOD get_space : DINT
(* =========== IMPLEMENTATION =========== *)
IF pBuffer <> 0 THEN
    get_space := udiCapacity - udiCurrentSize;
ELSE
    get_space := 0;
END_IF