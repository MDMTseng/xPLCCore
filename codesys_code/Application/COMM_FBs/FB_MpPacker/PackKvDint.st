METHOD PackKvDint : BOOL
VAR_INPUT
    psKey : POINTER TO STRING;
    diVal : DINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// See PackKvStr header. KV pair, key:DINT, bumps outer ele_count by 1.
pSlot^.whead := PackString(pStart := pSlot^.whead, psValue := psKey);
pSlot^.whead := PackDINT(pStart := pSlot^.whead, diValue := diVal);
pSlot^.ele_count := pSlot^.ele_count + 1;
PackKvDint := TRUE;
