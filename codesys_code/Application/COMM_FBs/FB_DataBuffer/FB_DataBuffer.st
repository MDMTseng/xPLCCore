(*
    FUNCTION_BLOCK FB_DataBuffer
    
    A re-usable data buffer that operates on an externally provided BYTE array.
    It provides methods for managing a continuous stream of bytes in that external memory.
*)
FUNCTION_BLOCK FB_DataBuffer

VAR
    (* Internal pointers and size information *)
    pBuffer : POINTER TO BYTE; (* Pointer to the start of the external buffer *)
    udiCapacity :DINT;       (* The logical capacity of the external buffer *)
    udiCurrentSize : DINT := 0; (* The current number of valid bytes in the buffer *)
END_VAR
(* =========== IMPLEMENTATION =========== *)
