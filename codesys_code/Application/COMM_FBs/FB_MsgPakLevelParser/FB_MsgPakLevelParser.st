(* ============================================================================
   FB_MsgPakLevelParser
   
   A state machine-based parser for MessagePack packets that processes data
   byte-by-byte and tracks the nesting level/depth of containers (arrays and maps).
   
   This parser does NOT decode MessagePack values, only tracks structure depth.
   ============================================================================ *)

FUNCTION_BLOCK FB_MsgPakLevelParser
VAR_INPUT
    in_byte : BYTE;  (* The byte to parse. Call this FB with a new byte for each step. *)
    x_reset : BOOL;  (* Set TRUE to reset the parser state. *)
END_VAR

VAR_OUTPUT
    stk_level_stack : ARRAY[0..29] OF UDINT;  (* Level stack showing remaining elements at each nesting level *)
    ui_level_stack_size : UDINT;              (* Current depth of the stack (number of nested containers) *)
    e_error : E_MsgPakLevelParser_ERROR;      (* Current error code (NONE if no error) *)
END_VAR

VAR
    (* State machine state *)
    e_state : E_MsgPakLevelParser_STATE;
    
    (* Length accumulation for variable-length types *)
    ui_length_bytes_remaining : UDINT;  (* How many length bytes still needed *)
    ui_length_value : UDINT;             (* Accumulated length value *)
    ui_data_bytes_remaining : UDINT;    (* How many data bytes still needed *)
    
    (* Track the type byte that caused us to enter EXPECT_LENGTH state *)
    by_last_type_byte : BYTE;  (* Last MessagePack type byte processed *)
    
    (* Constants *)
    c_MSGPACK_STACK_SIZE : UDINT := 30;  (* Maximum nesting depth *)
    
    (* Temporary variables *)
    by_current_byte : BYTE;
END_VAR
(* =========== IMPLEMENTATION =========== *)
(* ============================================================================
   Main State Machine Logic
   ============================================================================ *)

(* Handle reset *)
IF x_reset THEN
    ResetParser();
    RETURN;
END_IF

(* If error already occurred, stop processing *)
IF e_error <> E_MsgPakLevelParser_ERROR.NONE THEN
    RETURN;
END_IF

by_current_byte := in_byte;

CASE e_state OF
    E_MsgPakLevelParser_STATE.EXPECT_HEADER:
        (* Process MessagePack type byte *)
        HandleHeaderByte();
        
    E_MsgPakLevelParser_STATE.EXPECT_LENGTH:
        (* Accumulate length bytes *)
        ProcessLengthBytes();
        
    E_MsgPakLevelParser_STATE.EXPECT_EXT_TYPE:
        (* Consume extension type byte. 2026-06-23 code-review fix:
           if ui_data_bytes_remaining was set to 0 by ProcessLengthBytes
           (legal-but-empty ext payload), go straight back to header
           expectation. Pre-fix unconditionally transitioned to
           EXPECT_DATA -> ProcessDataBytes decremented from 0 ->
           UDINT underflow to 16#FFFFFFFF -> ~4 GB silent swallow. *)
        IF ui_data_bytes_remaining = 0 THEN
            DecrementStack();
            e_state := E_MsgPakLevelParser_STATE.EXPECT_HEADER;
        ELSE
            e_state := E_MsgPakLevelParser_STATE.EXPECT_DATA;
        END_IF
        
    E_MsgPakLevelParser_STATE.EXPECT_DATA:
        (* Consume data bytes *)
        ProcessDataBytes();
        
    ELSE
        e_error := E_MsgPakLevelParser_ERROR.INVALID_STATE;
END_CASE