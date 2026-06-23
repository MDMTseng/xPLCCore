METHOD PackElemStr : BOOL
VAR_INPUT
    psVal : POINTER TO STRING;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Pack one msgpack string value (no key, no count bump). Use INSIDE
// an array body opened by PackKvArray to write one element. The
// outer key + array header (PackKvArray) already counted itself.
IF bSlotOverflowed THEN PackElemStr := FALSE; RETURN; END_IF
pSlot^.whead := PackString(pStart := pSlot^.whead, psValue := psVal);
IF (pSlot^.whead - pSlot^.buf) > TO_DINT(pSlot^.buf_capacity) THEN
    bSlotOverflowed := TRUE;
    PackElemStr := FALSE;
ELSE
    PackElemStr := TRUE;
END_IF
