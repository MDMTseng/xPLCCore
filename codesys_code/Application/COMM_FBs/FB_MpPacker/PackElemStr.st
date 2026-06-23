METHOD PackElemStr : BOOL
VAR_INPUT
    psVal : POINTER TO STRING;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Pack one msgpack string value (no key, no count bump). Use INSIDE
// an array body opened by PackKvArray to write one element. The
// outer key + array header (PackKvArray) already counted itself.
pSlot^.whead := PackString(pStart := pSlot^.whead, psValue := psVal);
PackElemStr := TRUE;
