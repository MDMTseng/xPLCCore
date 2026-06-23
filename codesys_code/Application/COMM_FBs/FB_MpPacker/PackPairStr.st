METHOD PackPairStr : BOOL
VAR_INPUT
    psKey : POINTER TO STRING;
    psVal : POINTER TO STRING;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Nested-map KV pair (string value), NO outer ele_count bump.
// Use INSIDE a map opened by PackKvMap -- the outer KvMap counted
// itself; inner pairs are sub-content under that map's header.
pSlot^.whead := PackString(pStart := pSlot^.whead, psValue := psKey);
pSlot^.whead := PackString(pStart := pSlot^.whead, psValue := psVal);
PackPairStr := TRUE;
