METHOD PackPairBool : BOOL
VAR_INPUT
    psKey : POINTER TO STRING;
    bVal  : BOOL;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// See PackPairStr header. Nested-map KV, key:BOOL, no outer count bump.
pSlot^.whead := PackString(pStart := pSlot^.whead, psValue := psKey);
pSlot^.whead := PackBool(pStart := pSlot^.whead, bValue := bVal);
PackPairBool := TRUE;
