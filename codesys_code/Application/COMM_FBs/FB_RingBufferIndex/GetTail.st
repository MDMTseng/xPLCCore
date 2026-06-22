METHOD GetTail : DINT
VAR_INPUT
END_VAR
(* =========== IMPLEMENTATION =========== *)
    IF Size() = 0 THEN
        GetTail := -1; // Buffer empty
        RETURN;
    END_IF;

    GetTail := UINT_TO_DINT(_tail MOD _capacity);
