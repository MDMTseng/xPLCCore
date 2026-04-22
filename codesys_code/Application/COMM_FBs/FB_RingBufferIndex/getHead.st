METHOD getHead : DINT
(* =========== IMPLEMENTATION =========== *)

    // Check for initialization and if there is space
    IF (_count >= _capacity) THEN
        getHead := -1; // Return -1 to indicate buffer is full or not ready
        RETURN;
    END_IF;

    getHead := UINT_TO_DINT(_head);
