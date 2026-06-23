METHOD PackPairDint : BOOL
VAR_INPUT
    psKey : POINTER TO STRING;
    diVal : DINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// See PackPairStr header. Nested-map KV, no outer ele_count bump.
PackPairDint := TRUE;
ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := psKey);
ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := diVal);
