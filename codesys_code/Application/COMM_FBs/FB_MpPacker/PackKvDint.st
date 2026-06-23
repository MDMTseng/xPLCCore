METHOD PackKvDint : BOOL
VAR_INPUT
    psKey : POINTER TO STRING;
    diVal : DINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// See PackKvStr header. KV pair, key:DINT, bumps outer ele_count by 1.
IF bSlotOverflowed THEN PackKvDint := FALSE; RETURN; END_IF
pSlot^.whead := PackString(pStart := pSlot^.whead, psValue := psKey);
pSlot^.whead := PackDINT(pStart := pSlot^.whead, diValue := diVal);
pSlot^.ele_count := pSlot^.ele_count + 1;
IF (pSlot^.whead - pSlot^.buf) > TO_DINT(pSlot^.buf_capacity) THEN
    bSlotOverflowed := TRUE;
    PackKvDint := FALSE;
ELSE
    PackKvDint := TRUE;
END_IF
