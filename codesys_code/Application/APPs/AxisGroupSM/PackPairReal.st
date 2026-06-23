METHOD PackPairReal : BOOL
VAR_INPUT
    psKey : POINTER TO STRING;
    rVal  : REAL;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// See PackPairStr header. Nested-map KV, no outer ele_count bump.
PackPairReal := TRUE;
ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := psKey);
ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := rVal);
