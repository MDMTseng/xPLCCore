METHOD PackKvLint : BOOL
VAR_INPUT
    psKey : POINTER TO STRING;
    liVal : LINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Pack one msgpack key:LINT-value pair into the current
// ResponsePacketPointer slot. See PackKvStr header for rationale.
PackKvLint := TRUE;
ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := psKey);
ResponsePacketPointer^.whead := MessagePackerFb.PackLINT(pStart := ResponsePacketPointer^.whead, liValue := liVal);
ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 1;
