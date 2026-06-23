METHOD PackKvBool : BOOL
VAR_INPUT
    psKey : POINTER TO STRING;
    bVal  : BOOL;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// See PackKvStr header. KV pair, key:BOOL, bumps outer ele_count by 1.
IF bSlotOverflowed THEN PackKvBool := FALSE; RETURN; END_IF
pSlot^.whead := PackString(pStart := pSlot^.whead, psValue := psKey);
pSlot^.whead := PackBool(pStart := pSlot^.whead, bValue := bVal);
pSlot^.ele_count := pSlot^.ele_count + 1;
IF (pSlot^.whead - pSlot^.buf) > TO_DINT(pSlot^.buf_capacity) THEN
    bSlotOverflowed := TRUE;
    PackKvBool := FALSE;
ELSE
    PackKvBool := TRUE;
END_IF
