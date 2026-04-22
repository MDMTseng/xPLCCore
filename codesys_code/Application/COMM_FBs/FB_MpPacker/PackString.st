
METHOD PackString : POINTER TO BYTE
VAR_INPUT
	pStart 	: POINTER TO BYTE;
	// --- REVERTED: Input changed back to POINTER TO STRING ---
	// This allows passing ADR('literal') or ADR(variable).
	psValue : POINTER TO STRING; 
END_VAR
VAR
	nLen : UDINT;
	pWordLen : POINTER TO WORD;
	// Variables for manual length calculation
	pbyChar : POINTER TO BYTE;
	iLoop   : DINT;
	
	
	TMP:WORD;
END_VAR
	
(* =========== IMPLEMENTATION =========== *)
	
	// Handle NULL pointer case first
	IF psValue = 0 THEN
		PackString := pStart;
		RETURN;
	END_IF;

	// --- MANUAL LENGTH CALCULATION (USER VALIDATED) ---
	// This loop is essential because LEN(psValue^) may not reflect the true length
	// if the pointer's assumed STRING length is shorter than the actual string.
	nLen := 0;
	pbyChar := psValue; // Get address of the string data from the input pointer
	// Iterate with a large upper bound to ensure null terminator is found,
	// safeguarding against compiler-imposed pointer limits.
	FOR iLoop := 0 TO 999999 DO 
	    IF pbyChar^ = 0 THEN // Null terminator found
	        EXIT;
	    END_IF;
	    nLen := nLen + 1;
	    pbyChar := pbyChar + 1;
	END_FOR;
	// -------------------------------------------------
	
	IF nLen <= 31 THEN
		pStart^ := 16#A0 + UDINT_TO_BYTE(nLen);
		pStart := pStart + 1;
	ELSIF nLen <= 255 THEN
		pStart^ := 16#D9;
		pStart := pStart + 1;
		pStart^ := UDINT_TO_BYTE(nLen);
		pStart := pStart + 1;
	ELSIF nLen <= 65535 THEN
		pStart^ := 16#DA;
		pStart := pStart + 1;
		pWordLen := pStart;
		//pWordLen^ := SwapWORD(UDINT_TO_WORD(nLen));
		
		TMP:=UDINT_TO_WORD(nLen);
		Swap16To(ADR(TMP),pWordLen);
		pStart := pStart + 2;
	ELSE
		PackString := pStart; // String is too long for MessagePack formats
		RETURN;
	END_IF;

	// Copy string data using the original input pointer
	MEMUtils.MemCpy(pbyDest:=pStart, pbySrc:=psValue, dwSize:=UDINT_TO_DWORD(nLen));
	PackString := pStart + nLen;