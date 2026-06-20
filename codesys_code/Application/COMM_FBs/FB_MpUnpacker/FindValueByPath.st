METHOD PRIVATE FindValueByPath : BOOL
VAR_INPUT
	sPath		: STRING;
END_VAR
VAR_IN_OUT
	pResult		: POINTER TO BYTE;
END_VAR
VAR
	sRemainingPath : STRING(255) := sPath;
	sSegment : STRING(80);
	nSepPos, nBracketPos, nEndBracketPos, i, nTargetIndex, nCounter : DINT;
	nMapCount, nArrayCount : UINT;
	sKeyFound : STRING;
	fbTempUnpacker : FB_MpUnpacker;
	// Local pointer for cleaner dereferencing
	pWordLen : POINTER TO WORD; // Declared here
	pDwordLen : POINTER TO DWORD;
	udiTmpCount : UDINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
	FindValueByPath := FALSE;
	pResult := pStart;

	WHILE LEN(sRemainingPath) > 0 DO
		IF LEFT(sRemainingPath, 1) <> '[' THEN // This path segment is a key (for a map)
			nSepPos := FIND(sRemainingPath, '.');
			nBracketPos := FIND(sRemainingPath, '[');

			IF nSepPos > 0 AND (nBracketPos = 0 OR nSepPos < nBracketPos) THEN
				sSegment := LEFT(sRemainingPath, DINT_TO_INT(nSepPos - 1));
			ELSIF nBracketPos > 0 AND (nSepPos = 0 OR nBracketPos < nSepPos) THEN
				sSegment := LEFT(sRemainingPath, DINT_TO_INT(nBracketPos - 1));
			ELSE
				sSegment := sRemainingPath;
			END_IF;
			
			// Handle map header based on marker
			IF (pResult^ >= 16#80 AND pResult^ <= 16#8F) THEN // fixmap
				nMapCount := pResult^ - 16#80;
				pResult := pResult + 1;
			ELSIF pResult^ = 16#DE THEN // map 16
				// --- BUG FIX FOR MAP HEADER ---
				// This was the part still causing issues.
				pWordLen := pResult + 1; // Point to the 2-byte length field
				Swap16To(pWordLen,ADR(nMapCount));
				pResult := pResult + 3; // Advance pointer by 1 (marker) + 2 (count bytes)
			ELSIF pResult^ = 16#DF THEN // map 32
				pDwordLen := pResult + 1;
				Swap32To(pDwordLen, ADR(udiTmpCount));
				IF udiTmpCount > 16#FFFF THEN RETURN; END_IF; // path-search cap
				nMapCount := UDINT_TO_UINT(udiTmpCount);
				pResult := pResult + 5;
			ELSE
				// Current element is not a map, so path segment is invalid
				RETURN;
			END_IF;

			nCounter := 0; // Use nCounter to track if key is found, -1 if found
			FOR i := 1 TO nMapCount DO
				// Initialize a temporary unpacker for the key
				fbTempUnpacker.Init(pResult, udiSize - (pResult - pStart));
				fbTempUnpacker.UnpackNext(); // Unpack the key
				sKeyFound := fbTempUnpacker.sLastString; // Get the key string
				
				IF NOT SkipValue(pResult) THEN RETURN; END_IF; // pResult now points to the value

				IF sKeyFound = sSegment THEN
					nCounter := -1; // Key found, mark as -1 and exit loop
					EXIT;
				ELSE
					IF NOT SkipValue(pResult) THEN RETURN; END_IF; // Skip the value if key not found
				END_IF;
			END_FOR;
			
			IF nCounter <> -1 THEN RETURN; END_IF; // If key not found after loop, return FALSE
			
			sRemainingPath := RIGHT(sRemainingPath, DINT_TO_INT(LEN(sRemainingPath) - LEN(sSegment)));
		ELSE
			nEndBracketPos := FIND(sRemainingPath, ']');
			IF nEndBracketPos = 0 THEN RETURN; END_IF;
			
			// Extract the index string, e.g., "55" from "[55]".
			// CODESYS MID(IN, LEN, POS): LEN chars starting at POS (1-based).
			// Pre-fix this passed (2, N-2) -- LEN=2 swallowed the leading '['
			// for indices >= 1, silently returning element 0 for every path.
			sSegment := MID(sRemainingPath, DINT_TO_INT(nEndBracketPos - 2), 2);
			nTargetIndex := STRING_TO_DINT(sSegment);
			
			// Handle array header based on marker
			IF (pResult^ >= 16#90 AND pResult^ <= 16#9F) THEN // fixarray
				nArrayCount := pResult^ - 16#90;
				pResult := pResult + 1;
			ELSIF pResult^ = 16#DC THEN // array 16
				// --- PREVIOUS BUG FIX FOR ARRAY ---
				pWordLen := pResult + 1; // Point to the 2-byte length field

				Swap16To(pWordLen,ADR(nArrayCount));
				pResult := pResult + 3; // Advance pointer by 1 (marker) + 2 (count bytes)
			ELSIF pResult^ = 16#DD THEN // array 32
				pDwordLen := pResult + 1;
				Swap32To(pDwordLen, ADR(udiTmpCount));
				IF udiTmpCount > 16#FFFF THEN RETURN; END_IF; // path-search cap
				nArrayCount := UDINT_TO_UINT(udiTmpCount);
				pResult := pResult + 5;
			ELSE
				// Current element is not an array, so path segment is invalid
				RETURN;
			END_IF;

			// Check if the target index is within the array bounds
			IF nTargetIndex >= DINT_TO_UINT(nArrayCount) THEN RETURN; END_IF;

			// Skip elements until the target index is reached
			FOR i := 0 TO nTargetIndex - 1 DO
				IF NOT SkipValue(pResult) THEN RETURN; END_IF;
			END_FOR;
			
			sRemainingPath := RIGHT(sRemainingPath, DINT_TO_INT(LEN(sRemainingPath) - nEndBracketPos));
		

		END_IF;
		
		IF LEN(sRemainingPath) > 0 AND LEFT(sRemainingPath, 1) = '.' THEN
			sRemainingPath := RIGHT(sRemainingPath, DINT_TO_INT(LEN(sRemainingPath) - 1));
		END_IF;
	END_WHILE;

	FindValueByPath := TRUE;