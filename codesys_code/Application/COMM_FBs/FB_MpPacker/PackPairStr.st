METHOD PackPairStr : BOOL
VAR_INPUT
    psKey : POINTER TO STRING;
    psVal : POINTER TO STRING;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Nested-map KV pair (string value), NO outer ele_count bump.
// Use INSIDE a map opened by PackKvMap -- the outer KvMap counted
// itself; inner pairs are sub-content under that map's header.
IF bSlotOverflowed THEN PackPairStr := FALSE; RETURN; END_IF
pSlot^.whead := PackString(pStart := pSlot^.whead, psValue := psKey);
pSlot^.whead := PackString(pStart := pSlot^.whead, psValue := psVal);
IF (pSlot^.whead - pSlot^.buf) > TO_DINT(pSlot^.buf_capacity) THEN
    bSlotOverflowed := TRUE;
    PackPairStr := FALSE;
ELSE
    PackPairStr := TRUE;
END_IF
