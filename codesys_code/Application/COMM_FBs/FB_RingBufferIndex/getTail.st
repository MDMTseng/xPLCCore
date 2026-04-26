METHOD getTail : DINT
VAR_INPUT
END_VAR
(* =========== IMPLEMENTATION =========== *)
    IF size() = 0 THEN
        getTail := -1; // Buffer empty
        RETURN;
    END_IF;

    getTail := UINT_TO_DINT(_tail MOD _capacity);
