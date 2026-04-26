METHOD consumeTail : BOOL
VAR_INPUT
END_VAR
(* =========== IMPLEMENTATION =========== *)
    IF size() = 0 THEN
        consumeTail := FALSE; // Buffer empty
        RETURN;
    END_IF;

    // Consumer-only write; producer observes the new tail and learns a
    // slot freed up.
    _tail := (_tail + 1) MOD _cap2;

    consumeTail := TRUE;
