METHOD PRIVATE GetValueSize : UDINT
VAR_INPUT
	pObjectStart : POINTER TO BYTE;
END_VAR
VAR
	pTemp : POINTER TO BYTE;
END_VAR
(* =========== IMPLEMENTATION =========== *)
	pTemp := pObjectStart;
	IF SkipValue(pTemp) THEN
		GetValueSize := pTemp - pObjectStart;
	ELSE
		GetValueSize := 0;
	END_IF;