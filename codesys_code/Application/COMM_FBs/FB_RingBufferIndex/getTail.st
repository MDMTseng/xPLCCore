METHOD getTail : DINT
VAR_INPUT
END_VAR
(* =========== IMPLEMENTATION =========== *)
    // Check for initialization and if there are items to consume
    IF (_count = 0) THEN
        getTail := -1; // Return -1 to indicate buffer is empty or not ready
        RETURN;
    END_IF;

    getTail := UINT_TO_DINT(_tail);