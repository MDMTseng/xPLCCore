METHOD PackPairReal : BOOL
VAR_INPUT
    psKey : POINTER TO STRING;
    rVal  : REAL;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// See PackPairStr header. Nested-map KV, key:REAL, no outer count bump.
IF bSlotOverflowed THEN PackPairReal := FALSE; RETURN; END_IF
pSlot^.whead := PackString(pStart := pSlot^.whead, psValue := psKey);
pSlot^.whead := PackREAL(pStart := pSlot^.whead, rValue := rVal);
IF (pSlot^.whead - pSlot^.buf) > TO_DINT(pSlot^.buf_capacity) THEN
    bSlotOverflowed := TRUE;
    PackPairReal := FALSE;
ELSE
    PackPairReal := TRUE;
END_IF
