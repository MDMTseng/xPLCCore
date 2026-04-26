METHOD getHead : DINT
(* =========== IMPLEMENTATION =========== *)

    // Full check via derived size; safer than read-modify-write _count.
    IF size() >= _capacity THEN
        getHead := -1; // Buffer full
        RETURN;
    END_IF;

    // External slot index lives in [0.._capacity-1]; internal _head
    // lives in [0.._cap2-1] for empty/full disambiguation.
    getHead := UINT_TO_DINT(_head MOD _capacity);
