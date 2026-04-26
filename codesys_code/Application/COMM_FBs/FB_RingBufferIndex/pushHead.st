METHOD pushHead : BOOL
VAR_INPUT
END_VAR
(* =========== IMPLEMENTATION =========== *)

    IF size() >= _capacity THEN
        pushHead := FALSE; // Buffer full
        RETURN;
    END_IF;

    // Producer-only write; pairs with the producer's prior data write
    // into slot[getHead()]. Consumer observes the new head and only
    // then dereferences the slot.
    _head := (_head + 1) MOD _cap2;

    pushHead := TRUE;
