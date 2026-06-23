METHOD PackPairStr : BOOL
VAR_INPUT
    psKey : POINTER TO STRING;
    psVal : POINTER TO STRING;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Pack one msgpack key:string-value pair into the current
// ResponsePacketPointer slot WITHOUT touching the outer ele_count.
// Use for content INSIDE a nested map -- the outer map's PackKvMap
// already counted itself; the inner pairs are sub-content.
PackPairStr := TRUE;
ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := psKey);
ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := psVal);
