METHOD PackKvMap : BOOL
VAR_INPUT
    psKey    : POINTER TO STRING;
    uiInnerN : UINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Pack one OUTER key + nested-map header into the bound slot, bump
// outer ele_count by 1. Caller follows up with PackPair* calls to
// fill the inner map. uiInnerN MUST equal the subsequent PackPair*
// count (msgpack readers trust this header, no trailer marker).
IF bSlotOverflowed THEN PackKvMap := FALSE; RETURN; END_IF
pSlot^.whead := PackString(pStart := pSlot^.whead, psValue := psKey);
pSlot^.whead := PackMapHeader(pStart := pSlot^.whead, uiCount := uiInnerN);
pSlot^.ele_count := pSlot^.ele_count + 1;
IF (pSlot^.whead - pSlot^.buf) > TO_DINT(pSlot^.buf_capacity) THEN
    bSlotOverflowed := TRUE;
    PackKvMap := FALSE;
ELSE
    PackKvMap := TRUE;
END_IF
