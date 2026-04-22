METHOD PackMapHeader : POINTER TO BYTE
VAR_INPUT
	pStart : POINTER TO BYTE;
	uiCount : UINT;
	bForceLargeField:BOOL:=FALSE;
END_VAR
VAR
	pWordCount : POINTER TO WORD;
END_VAR
(* =========== IMPLEMENTATION =========== *)
	IF uiCount <= 15 AND bForceLargeField=FALSE THEN
		pStart^ := 16#80 + UINT_TO_BYTE(uiCount);
		PackMapHeader := pStart + 1;
	ELSIF uiCount <= 65535 OR bForceLargeField=TRUE THEN
		pStart^ := 16#DE;
		pStart := pStart + 1;
		pWordCount := pStart;
		Swap16To(ADR(uiCount),pWordCount);
		PackMapHeader := pStart + 2;
	END_IF;