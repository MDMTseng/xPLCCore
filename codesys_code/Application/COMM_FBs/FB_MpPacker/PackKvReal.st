METHOD PackKvReal : BOOL
VAR_INPUT
    psKey : POINTER TO STRING;
    rVal  : REAL;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// See PackKvStr header. KV pair, key:REAL, bumps outer ele_count by 1.
IF bSlotOverflowed THEN PackKvReal := FALSE; RETURN; END_IF
pSlot^.whead := PackString(pStart := pSlot^.whead, psValue := psKey);
pSlot^.whead := PackREAL(pStart := pSlot^.whead, rValue := rVal);
pSlot^.ele_count := pSlot^.ele_count + 1;
IF (pSlot^.whead - pSlot^.buf) > TO_DINT(pSlot^.buf_capacity) THEN
    bSlotOverflowed := TRUE;
    PackKvReal := FALSE;
ELSE
    PackKvReal := TRUE;
END_IF
