(*
    Fail: increment uiFailed. On first failure only, copy the message
    into sFirstFail so the cause is visible in online mode without
    needing a trace.
*)
METHOD PRIVATE Fail
VAR_INPUT
    sMsg : STRING(250);
END_VAR
(* =========== IMPLEMENTATION =========== *)
IF uiFailed = 0 THEN
    sFirstFail := sMsg;
END_IF
uiFailed := uiFailed + 1;
