METHOD PackPairLint : BOOL
VAR_INPUT
    psKey : POINTER TO STRING;
    liVal : LINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// See PackPairStr header. Nested-map KV, key:LINT, no outer count bump.
IF bSlotOverflowed THEN PackPairLint := FALSE; RETURN; END_IF
pSlot^.whead := PackString(pStart := pSlot^.whead, psValue := psKey);
pSlot^.whead := PackLINT(pStart := pSlot^.whead, liValue := liVal);
IF (pSlot^.whead - pSlot^.buf) > TO_DINT(pSlot^.buf_capacity) THEN
    bSlotOverflowed := TRUE;
    PackPairLint := FALSE;
ELSE
    PackPairLint := TRUE;
END_IF
