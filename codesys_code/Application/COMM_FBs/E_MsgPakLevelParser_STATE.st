(* ============================================================================
   State Enumeration for FB_MsgPakLevelParser State Machine
   ============================================================================ *)

TYPE E_MsgPakLevelParser_STATE :
(
    EXPECT_HEADER := 0,     (* Waiting for a new MessagePack type byte *)
    EXPECT_LENGTH := 1,     (* Accumulating length bytes for variable-length types *)
    EXPECT_EXT_TYPE := 2,   (* Waiting for extension type byte *)
    EXPECT_DATA := 3        (* Consuming data bytes (strings, binaries, etc.) *)
);
END_TYPE

