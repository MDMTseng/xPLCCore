METHOD PackKvArray : BOOL
VAR_INPUT
    psKey    : POINTER TO STRING;
    uiInnerN : UINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Pack one OUTER msgpack key + nested-array header at the current
// ResponsePacketPointer slot, bump outer ele_count by 1.
// Caller follows up with `PackElem*(...)` calls to fill the array.
// uiInnerN MUST equal the count of subsequent PackElem* calls.
PackKvArray := TRUE;
ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := psKey);
ResponsePacketPointer^.whead := MessagePackerFb.PackArrayHeader(pStart := ResponsePacketPointer^.whead, uiCount := uiInnerN);
ResponsePacketPointer^.ele_count := ResponsePacketPointer^.ele_count + 1;
