METHOD DecrementStack : BOOL
VAR_INPUT
END_VAR
(* =========== IMPLEMENTATION =========== *)

(* Recursively decrement and pop completed levels *)
WHILE ui_level_stack_size > 0 DO
    (* Decrement current level *)
    IF stk_level_stack[ui_level_stack_size - 1] > 0 THEN
        stk_level_stack[ui_level_stack_size - 1] := stk_level_stack[ui_level_stack_size - 1] - 1;
    END_IF
    
    (* If items still remain in this container, we are done *)
    IF stk_level_stack[ui_level_stack_size - 1] > 0 THEN
        EXIT;
    END_IF
    
    (* If zero, pop this level and continue to decrement parent *)
    ui_level_stack_size := ui_level_stack_size - 1;
END_WHILE