METHOD PackElemStr : BOOL
VAR_INPUT
    psVal : POINTER TO STRING;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Pack one msgpack string value (no key, no count bump). Use INSIDE
// an array body to write one element. The outer key + array header
// (PackKvArray) already counted itself.
PackElemStr := TRUE;
ResponsePacketPointer^.whead := MessagePackerFb.PackString(pStart := ResponsePacketPointer^.whead, psValue := psVal);
