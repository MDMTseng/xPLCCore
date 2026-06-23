METHOD PackPairLint : BOOL
VAR_INPUT
    psKey : POINTER TO STRING;
    liVal : LINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// See PackPairStr header. Nested-map KV, no outer ele_count bump.
PackPairLint := TRUE;
ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := psKey);
ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := liVal);
