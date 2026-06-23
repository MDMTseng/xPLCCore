METHOD PackKvLint : BOOL
VAR_INPUT
    psKey : POINTER TO STRING;
    liVal : LINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// See PackKvStr header. KV pair, key:LINT, bumps outer ele_count by 1.
IF bSlotOverflowed THEN PackKvLint := FALSE; RETURN; END_IF
pSlot^.whead := PackString(pStart := pSlot^.whead, psValue := psKey);
pSlot^.whead := PackLINT(pStart := pSlot^.whead, liValue := liVal);
pSlot^.ele_count := pSlot^.ele_count + 1;
IF (pSlot^.whead - pSlot^.buf) > TO_DINT(pSlot^.buf_capacity) THEN
    bSlotOverflowed := TRUE;
    PackKvLint := FALSE;
ELSE
    PackKvLint := TRUE;
END_IF
