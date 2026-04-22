METHOD PackNil : POINTER TO BYTE
VAR_INPUT
	pStart : POINTER TO BYTE;
END_VAR
(* =========== IMPLEMENTATION =========== *)
pStart^ := 16#C0; // nil format
PackNil := pStart + 1;