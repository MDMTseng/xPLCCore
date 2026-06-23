METHOD PackKvStr : BOOL
VAR_INPUT
    psKey : POINTER TO STRING;
    psVal : POINTER TO STRING;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Pack one msgpack key:string-value pair into the current
// ResponsePacketPointer slot, advance whead, bump ele_count by 1.
// 2026-06-23: introduced to collapse the ~30 verbose
//   ResponsePacketPointer^.whead := MessagePackerFb.PackString(
//       pStart := ResponsePacketPointer^.whead, psValue := ADR('xxx'));
// triples that pepper DrainHostPackets / ProcessMotionPacket. Also
// fixes the "ele_count := N literal must match the K/V count below"
// bug-bait by always +=1 here.
PackKvStr := TRUE;
ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := psKey);
ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := psVal);
ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 1;
