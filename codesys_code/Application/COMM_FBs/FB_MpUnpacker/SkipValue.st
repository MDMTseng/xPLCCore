(*
Method: SkipValue [FINAL CORRECTED ITERATIVE VERSION]
Description: Advances a given pointer past the next complete value in the stream.
             This robust iterative version uses a simple counter to avoid recursion
             and correctly handle all MessagePack types.
Return Type: BOOL (TRUE if successful)
*)
METHOD PRIVATE SkipValue : BOOL
VAR_IN_OUT
	pPos : POINTER TO BYTE;
END_VAR
VAR
	diItemsToSkip : DINT := 1; // Start by wanting to skip 1 top-level item.
	byMarker : BYTE;
	udiLength : UDINT;
	uiCount : UINT;
	pWordLen : POINTER TO WORD;
	
    pDwordLen 	: POINTER TO DWORD;
    udiCount32 	: UDINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
	WHILE diItemsToSkip > 0 DO
		// Boundary check for each item to be skipped
		IF pPos >= (pStart + udiSize) THEN
			SkipValue := FALSE;
			RETURN;
		END_IF;

		diItemsToSkip := diItemsToSkip - 1; // Consume one item from the "to-skip" list

		byMarker := pPos^; // Read the marker of the current item
		pPos := pPos + 1;  // Advance pointer past the marker
		
		
		
		CASE byMarker OF
    // Nil, Bool, FixInt (0-byte payload, pointer already advanced)
    16#C0, 16#C2, 16#C3: CONTINUE;

    // --- Fixed-size Payloads ---
    16#CC, 16#D0: pPos := pPos + 1; // uint 8, int 8
    16#CD, 16#D1: pPos := pPos + 2; // uint 16, int 16
    16#CE, 16#D2, 16#CA: pPos := pPos + 4; // uint 32, int 32, float 32
    16#CF, 16#D3, 16#CB: pPos := pPos + 8; // uint 64, int 64, float 64

    // --- String Types (length-driven; clamp to remaining buffer so a malformed
    //     payload can't push pPos past pStart+udiSize) ---
    16#D9: // str 8
        udiLength := pPos^;
        pPos := pPos + 1;
        IF (pPos + udiLength) > (pStart + udiSize) THEN
            udiLength := DWORD_TO_UDINT((pStart + udiSize) - pPos);
        END_IF;
        pPos := pPos + udiLength;
    16#DA: // str 16
        pWordLen := pPos;
        udiLength := 0; // Swap16To writes only the low 2 bytes; clear upper bytes from any prior 32-bit length read in this WHILE iteration to avoid an OR'd-in stale value (code review fix 2026-06-23).
        Swap16To(pWordLen, ADR(udiLength)); // Assumes udiLength gets lower 16 bits
        pPos := pPos + 2;
        IF (pPos + udiLength) > (pStart + udiSize) THEN
            udiLength := DWORD_TO_UDINT((pStart + udiSize) - pPos);
        END_IF;
        pPos := pPos + udiLength;
    16#DB: // str 32
        pDwordLen := pPos;
        Swap32To(pDwordLen, ADR(udiLength)); // Assumes Swap32To helper exists
        pPos := pPos + 4;
        IF (pPos + udiLength) > (pStart + udiSize) THEN
            udiLength := DWORD_TO_UDINT((pStart + udiSize) - pPos);
        END_IF;
        pPos := pPos + udiLength;

    // --- Binary Types (same clamp pattern) ---
    16#C4: // bin 8
        udiLength := pPos^;
        pPos := pPos + 1;
        IF (pPos + udiLength) > (pStart + udiSize) THEN
            udiLength := DWORD_TO_UDINT((pStart + udiSize) - pPos);
        END_IF;
        pPos := pPos + udiLength;
    16#C5: // bin 16
        pWordLen := pPos;
        udiLength := 0; // see str16 note: clear high bytes before Swap16To
        Swap16To(pWordLen, ADR(udiLength));
        pPos := pPos + 2;
        IF (pPos + udiLength) > (pStart + udiSize) THEN
            udiLength := DWORD_TO_UDINT((pStart + udiSize) - pPos);
        END_IF;
        pPos := pPos + udiLength;
    16#C6: // bin 32
        pDwordLen := pPos;
        Swap32To(pDwordLen, ADR(udiLength));
        pPos := pPos + 4;
        IF (pPos + udiLength) > (pStart + udiSize) THEN
            udiLength := DWORD_TO_UDINT((pStart + udiSize) - pPos);
        END_IF;
        pPos := pPos + udiLength;

    // --- Extension Types (same clamp pattern for length-driven variants) ---
    16#D4: pPos := pPos + 2;  // fixext 1 (1-byte type + 1-byte data)
    16#D5: pPos := pPos + 3;  // fixext 2
    16#D6: pPos := pPos + 5;  // fixext 4
    16#D7: pPos := pPos + 9;  // fixext 8
    16#D8: pPos := pPos + 17; // fixext 16
    16#C7: // ext 8
        udiLength := pPos^;
        pPos := pPos + 2; // len(1) + type(1)
        IF (pPos + udiLength) > (pStart + udiSize) THEN
            udiLength := DWORD_TO_UDINT((pStart + udiSize) - pPos);
        END_IF;
        pPos := pPos + udiLength;
    16#C8: // ext 16
        pWordLen := pPos;
        udiLength := 0; // see str16 note: clear high bytes before Swap16To
        Swap16To(pWordLen, ADR(udiLength));
        pPos := pPos + 3; // len(2) + type(1)
        IF (pPos + udiLength) > (pStart + udiSize) THEN
            udiLength := DWORD_TO_UDINT((pStart + udiSize) - pPos);
        END_IF;
        pPos := pPos + udiLength;
    16#C9: // ext 32
        pDwordLen := pPos;
        Swap32To(pDwordLen, ADR(udiLength));
        pPos := pPos + 5; // len(4) + type(1)
        IF (pPos + udiLength) > (pStart + udiSize) THEN
            udiLength := DWORD_TO_UDINT((pStart + udiSize) - pPos);
        END_IF;
        pPos := pPos + udiLength;

ELSE
    IF (byMarker <= 16#7F) THEN // positive fixint
        CONTINUE;
    ELSIF (byMarker >= 16#E0) THEN // negative fixint
        CONTINUE;

    // fixstr
    ELSIF (byMarker >= 16#A0 AND byMarker <= 16#BF) THEN
        udiLength := byMarker - 16#A0;
        IF (pPos + udiLength) > (pStart + udiSize) THEN
            udiLength := DWORD_TO_UDINT((pStart + udiSize) - pPos);
        END_IF;
        pPos := pPos + udiLength;
        
    // --- Container Types (add children to skip list) ---
    ELSIF (byMarker >= 16#90 AND byMarker <= 16#9F) THEN // fixarray
        uiCount := byMarker - 16#90;
        diItemsToSkip := diItemsToSkip + uiCount;
    ELSIF (byMarker = 16#DC) THEN // array 16
        pWordLen := pPos;
        Swap16To(pWordLen, ADR(uiCount));
        pPos := pPos + 2;
        diItemsToSkip := diItemsToSkip + uiCount;
    ELSIF (byMarker = 16#DD) THEN // array 32
        pDwordLen := pPos;
        Swap32To(pDwordLen, ADR(udiCount32));
        pPos := pPos + 4;
        diItemsToSkip := diItemsToSkip + udiCount32;

    ELSIF (byMarker >= 16#80 AND byMarker <= 16#8F) THEN // fixmap
        uiCount := (byMarker - 16#80);
        diItemsToSkip := diItemsToSkip + (uiCount * 2);
    ELSIF (byMarker = 16#DE) THEN // map 16
        pWordLen := pPos;
        Swap16To(pWordLen, ADR(uiCount));
        pPos := pPos + 2;
        diItemsToSkip := diItemsToSkip + (uiCount * 2);
    ELSIF (byMarker = 16#DF) THEN // map 32
        pDwordLen := pPos;
        Swap32To(pDwordLen, ADR(udiCount32));
        pPos := pPos + 4;
        diItemsToSkip := diItemsToSkip + (udiCount32 * 2);
    ELSE
        // Unknown marker, cannot safely skip.
        SkipValue := FALSE;
        RETURN;
    END_IF;
END_CASE;

	END_WHILE;
	
	SkipValue := TRUE;