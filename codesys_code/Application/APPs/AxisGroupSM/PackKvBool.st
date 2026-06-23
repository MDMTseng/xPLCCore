METHOD PackKvBool : BOOL
VAR_INPUT
    psKey : POINTER TO STRING;
    bVal  : BOOL;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Pack one msgpack key:BOOL-value pair into the current
// ResponsePacketPointer slot. See PackKvStr header for rationale.
PackKvBool := TRUE;
ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := psKey);
ResponsePacketPointer^.whead := MessagePackerFb.PackBool(pStart := ResponsePacketPointer^.whead, bValue := bVal);
ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 1;
