METHOD PackPairDint : BOOL
VAR_INPUT
    psKey : POINTER TO STRING;
    diVal : DINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// See PackPairStr header. Nested-map KV, key:DINT, no outer count bump.
IF bSlotOverflowed THEN PackPairDint := FALSE; RETURN; END_IF
pSlot^.whead := PackString(pStart := pSlot^.whead, psValue := psKey);
pSlot^.whead := PackDINT(pStart := pSlot^.whead, diValue := diVal);
IF (pSlot^.whead - pSlot^.buf) > TO_DINT(pSlot^.buf_capacity) THEN
    bSlotOverflowed := TRUE;
    PackPairDint := FALSE;
ELSE
    PackPairDint := TRUE;
END_IF
