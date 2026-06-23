METHOD PackElemDint : BOOL
VAR_INPUT
    diVal : DINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// See PackElemStr header. Array-element DINT value, no key, no bump.
pSlot^.whead := PackDINT(pStart := pSlot^.whead, diValue := diVal);
PackElemDint := TRUE;
