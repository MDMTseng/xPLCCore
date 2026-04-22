METHOD ProcessDataBytes : BOOL
VAR_INPUT
END_VAR
(* =========== IMPLEMENTATION =========== *)

(* Optimized: combine decrement and check *)
ui_data_bytes_remaining := ui_data_bytes_remaining - 1;

IF ui_data_bytes_remaining = 0 THEN
    (* Data complete *)
    DecrementStack();
    e_state := E_MsgPakLevelParser_STATE.EXPECT_HEADER;
END_IF