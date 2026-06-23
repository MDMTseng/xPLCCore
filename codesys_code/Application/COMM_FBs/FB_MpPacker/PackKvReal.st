METHOD PackKvReal : BOOL
VAR_INPUT
    psKey : POINTER TO STRING;
    rVal  : REAL;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// See PackKvStr header. KV pair, key:REAL, bumps outer ele_count by 1.
pSlot^.whead := PackString(pStart := pSlot^.whead, psValue := psKey);
pSlot^.whead := PackREAL(pStart := pSlot^.whead, rValue := rVal);
pSlot^.ele_count := pSlot^.ele_count + 1;
PackKvReal := TRUE;
