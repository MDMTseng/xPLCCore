METHOD PackPairBool : BOOL
VAR_INPUT
    psKey : POINTER TO STRING;
    bVal  : BOOL;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// See PackPairStr header. Nested-map KV, key:BOOL, no outer count bump.
IF bSlotOverflowed THEN PackPairBool := FALSE; RETURN; END_IF
pSlot^.whead := PackString(pStart := pSlot^.whead, psValue := psKey);
pSlot^.whead := PackBool(pStart := pSlot^.whead, bValue := bVal);
IF (pSlot^.whead - pSlot^.buf) > TO_DINT(pSlot^.buf_capacity) THEN
    bSlotOverflowed := TRUE;
    PackPairBool := FALSE;
ELSE
    PackPairBool := TRUE;
END_IF
