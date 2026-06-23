METHOD PackKvDint : BOOL
VAR_INPUT
    psKey : POINTER TO STRING;
    diVal : DINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Pack one msgpack key:DINT-value pair into the current
// ResponsePacketPointer slot. See PackKvStr header for rationale.
PackKvDint := TRUE;
ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := psKey);
ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := diVal);
ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 1;
