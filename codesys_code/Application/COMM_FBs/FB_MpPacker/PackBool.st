METHOD PackBool : POINTER TO BYTE
VAR_INPUT
	pStart : POINTER TO BYTE;
	bValue : BOOL;
END_VAR
(* =========== IMPLEMENTATION =========== *)
	IF bValue THEN
		pStart^ := 16#C3; // true format
	ELSE
		pStart^ := 16#C2; // false format
	END_IF
	PackBool := pStart + 1;