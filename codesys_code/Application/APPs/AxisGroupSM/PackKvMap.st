METHOD PackKvMap : BOOL
VAR_INPUT
    psKey      : POINTER TO STRING;
    uiInnerN   : UINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Pack one OUTER msgpack key + nested-map header at the current
// ResponsePacketPointer slot, bump outer ele_count by 1.
// Caller follows up with `PackPair*(...)` calls to fill the inner
// map. uiInnerN MUST equal the count of subsequent PackPair* calls
// (msgpack readers trust this header count, no trailer marker).
PackKvMap := TRUE;
ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := psKey);
ResponsePacketPointer^.whead := MessagePackerFb.PackMapHeader(pStart := ResponsePacketPointer^.whead, uiCount := uiInnerN);
ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 1;
