METHOD ResetParser : BOOL
VAR_INPUT
END_VAR
(* =========== IMPLEMENTATION =========== *)
(* Reset state machine *)
e_state := E_MsgPakLevelParser_STATE.EXPECT_HEADER;

(* Reset stack *)
ui_level_stack_size := 0;

(* Reset length accumulation *)
ui_length_bytes_remaining := 0;
ui_length_value := 0;
ui_data_bytes_remaining := 0;

(* Reset type tracking *)
by_last_type_byte := 0;

(* Reset error *)
e_error := E_MsgPakLevelParser_ERROR.NONE;

