METHOD PackArrayHeader : POINTER TO BYTE
VAR_INPUT
	pStart : POINTER TO BYTE;
	uiCount : UINT;
END_VAR
VAR
	pWordCount : POINTER TO WORD;
END_VAR
(* =========== IMPLEMENTATION =========== *)
	IF uiCount <= 15 THEN
		pStart^ := 16#90 + UINT_TO_BYTE(uiCount);
		PackArrayHeader := pStart + 1;
	ELSIF uiCount <= 65535 THEN
		pStart^ := 16#DC;
		pStart := pStart + 1;
		pWordCount := pStart;
		Swap16To(ADR(uiCount),pWordCount);
		PackArrayHeader := pStart + 2;
	END_IF;