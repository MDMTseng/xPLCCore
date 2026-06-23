METHOD PackKvReal : BOOL
VAR_INPUT
    psKey : POINTER TO STRING;
    rVal  : REAL;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Pack one msgpack key:REAL-value pair into the current
// ResponsePacketPointer slot. See PackKvStr header for rationale.
PackKvReal := TRUE;
ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := psKey);
ResponsePacketPointer^.whead := MessagePackerFb.PackREAL(pStart := ResponsePacketPointer^.whead, rValue := rVal);
ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 1;
