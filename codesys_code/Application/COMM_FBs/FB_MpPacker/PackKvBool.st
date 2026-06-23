METHOD PackKvBool : BOOL
VAR_INPUT
    psKey : POINTER TO STRING;
    bVal  : BOOL;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// See PackKvStr header. KV pair, key:BOOL, bumps outer ele_count by 1.
pSlot^.whead := PackString(pStart := pSlot^.whead, psValue := psKey);
pSlot^.whead := PackBool(pStart := pSlot^.whead, bValue := bVal);
pSlot^.ele_count := pSlot^.ele_count + 1;
PackKvBool := TRUE;
