METHOD PackKvLint : BOOL
VAR_INPUT
    psKey : POINTER TO STRING;
    liVal : LINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// See PackKvStr header. KV pair, key:LINT, bumps outer ele_count by 1.
pSlot^.whead := PackString(pStart := pSlot^.whead, psValue := psKey);
pSlot^.whead := PackLINT(pStart := pSlot^.whead, liValue := liVal);
pSlot^.ele_count := pSlot^.ele_count + 1;
PackKvLint := TRUE;
