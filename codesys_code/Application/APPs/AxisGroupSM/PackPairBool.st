METHOD PackPairBool : BOOL
VAR_INPUT
    psKey : POINTER TO STRING;
    bVal  : BOOL;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// See PackPairStr header. Nested-map KV, no outer ele_count bump.
PackPairBool := TRUE;
ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := psKey);
ResponsePacketPointer^.whead := MessagePackerFb.PackBool(pStart := ResponsePacketPointer^.whead, bValue := bVal);
