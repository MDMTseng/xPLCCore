METHOD PackElemDint : BOOL
VAR_INPUT
    diVal : DINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// See PackElemStr header. Array-element DINT value, no key, no bump.
IF bSlotOverflowed THEN PackElemDint := FALSE; RETURN; END_IF
pSlot^.whead := PackDINT(pStart := pSlot^.whead, diValue := diVal);
IF (pSlot^.whead - pSlot^.buf) > TO_DINT(pSlot^.buf_capacity) THEN
    bSlotOverflowed := TRUE;
    PackElemDint := FALSE;
ELSE
    PackElemDint := TRUE;
END_IF
