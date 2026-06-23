METHOD PackPairReal : BOOL
VAR_INPUT
    psKey : POINTER TO STRING;
    rVal  : REAL;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// See PackPairStr header. Nested-map KV, key:REAL, no outer count bump.
pSlot^.whead := PackString(pStart := pSlot^.whead, psValue := psKey);
pSlot^.whead := PackREAL(pStart := pSlot^.whead, rValue := rVal);
PackPairReal := TRUE;
