METHOD PackKvArray : BOOL
VAR_INPUT
    psKey    : POINTER TO STRING;
    uiInnerN : UINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Pack one OUTER key + nested-array header into the bound slot, bump
// outer ele_count by 1. Caller follows up with PackElem* calls to fill
// the array. uiInnerN MUST equal the subsequent PackElem* count.
IF bSlotOverflowed THEN PackKvArray := FALSE; RETURN; END_IF
pSlot^.whead := PackString(pStart := pSlot^.whead, psValue := psKey);
pSlot^.whead := PackArrayHeader(pStart := pSlot^.whead, uiCount := uiInnerN);
pSlot^.ele_count := pSlot^.ele_count + 1;
IF (pSlot^.whead - pSlot^.buf) > TO_DINT(pSlot^.buf_capacity) THEN
    bSlotOverflowed := TRUE;
    PackKvArray := FALSE;
ELSE
    PackKvArray := TRUE;
END_IF
