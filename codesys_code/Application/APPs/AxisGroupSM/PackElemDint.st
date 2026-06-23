METHOD PackElemDint : BOOL
VAR_INPUT
    diVal : DINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// See PackElemStr header. Array-element DINT value.
PackElemDint := TRUE;
ResponsePacketPointer^.whead := MessagePackerFb.PackDINT(pStart := ResponsePacketPointer^.whead, diValue := diVal);
