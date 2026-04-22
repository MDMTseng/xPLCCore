METHOD PushStack : BOOL
VAR_INPUT
    ui_count : UDINT;  (* Number of elements to push *)
END_VAR
VAR
END_VAR
(* =========== IMPLEMENTATION =========== *)
IF ui_level_stack_size >= c_MSGPACK_STACK_SIZE THEN
    e_error := E_MsgPakLevelParser_ERROR.STACK_OVERFLOW;
    RETURN;
END_IF

stk_level_stack[ui_level_stack_size] := ui_count;
ui_level_stack_size := ui_level_stack_size + 1;
