(* ============================================================================
   Error Enumeration for FB_MsgPakLevelParser
   ============================================================================ *)

TYPE E_MsgPakLevelParser_ERROR :
(
    NONE := 0,              (* No error *)
    UNEXPECTED_BYTE := 1,   (* Invalid MessagePack type byte *)
    STACK_OVERFLOW := 2,    (* Too many nested containers *)
    INVALID_STATE := 3      (* Internal parser state error *)
);
END_TYPE

