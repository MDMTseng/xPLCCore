METHOD UnpackNext : E_MpType
VAR
	byMarker : BYTE;
	udiLength : UDINT;
	diTemp : DINT;
	rTemp : REAL;
	pWordLen : POINTER TO WORD;
	pDwordLen : POINTER TO DWORD;
	// Additional temp variables for big numbers
	tmpByte : BYTE;
	tmpSint : SINT;
	tmpInt : INT;
	tmpWord : WORD;
	tmpUdint : UDINT;
	tmpLint : LINT;
	tmpULint : ULINT;
	tmpLReal : LREAL;
	udiCount32 : UDINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
eLastType := E_MpType.MP_UNKNOWN;
	UnpackNext := E_MpType.MP_UNKNOWN;

	IF pCurrent >= (pStart + udiSize) THEN RETURN; END_IF;

	byMarker := pCurrent^;
	pCurrent := pCurrent + 1;

	CASE byMarker OF
		// --- Nil ---
		16#C0: eLastType := E_MpType.MP_NIL;
		
		// --- Boolean ---
		16#C2: eLastType := E_MpType.MP_BOOL; diLastDINT := 0;
		16#C3: eLastType := E_MpType.MP_BOOL; diLastDINT := 1;
		
		// --- Unsigned Integers ---
		16#CC: // uint 8
			tmpByte := pCurrent^;
			eLastType := E_MpType.MP_DINT; // Store as DINT (fits)
			diLastDINT := BYTE_TO_DINT(tmpByte);
			pCurrent := pCurrent + 1;
			
		16#CD: // uint 16
			pWordLen := pCurrent;
			Swap16To(pWordLen, ADR(tmpWord));
			eLastType := E_MpType.MP_DINT; // Store as DINT (fits)
			diLastDINT := WORD_TO_DINT(tmpWord);
			pCurrent := pCurrent + 2;
			
		16#CE: // uint 32
			pDwordLen := pCurrent;
			Swap32To(pDwordLen, ADR(tmpUdint));
			eLastType := E_MpType.MP_DINT; // Store as DINT (may truncate if > 2^31-1)
			IF tmpUdint > 2147483647 THEN // MAX_DINT
				diLastDINT := 2147483647; // Clamp to max DINT
			ELSE
				diLastDINT := UDINT_TO_DINT(tmpUdint);
			END_IF;
			pCurrent := pCurrent + 4;
			
		16#CF: // uint 64
			Swap64To(srcData:=pCurrent, dstData:=ADR(tmpULint));
			eLastType := E_MpType.MP_DINT; // Store as DINT (will truncate)
			IF tmpULint > 2147483647 THEN // MAX_DINT
				diLastDINT := 2147483647; // Clamp to max DINT
			ELSE
				diLastDINT := ULINT_TO_DINT(tmpULint);
			END_IF;
			pCurrent := pCurrent + 8;
		
		// --- Signed Integers ---
		16#D0: // int 8
			tmpSint := pCurrent^;
			eLastType := E_MpType.MP_DINT;
			diLastDINT := SINT_TO_DINT(tmpSint);
			pCurrent := pCurrent + 1;
			
		16#D1: // int 16
			pWordLen := pCurrent;
			Swap16To(pWordLen, ADR(tmpInt));
			eLastType := E_MpType.MP_DINT;
			diLastDINT := INT_TO_DINT(tmpInt);
			pCurrent := pCurrent + 2;
			
		16#D2: // int 32
			eLastType := E_MpType.MP_DINT;
			MEMUtils.MemCpy(pbyDest:=ADR(diTemp), pbySrc:=pCurrent, dwSize:=SIZEOF(diTemp));
			Swap32To(ADR(diTemp), ADR(diLastDINT));
			pCurrent := pCurrent + 4;
			
		16#D3: // int 64
			Swap64To(srcData:=pCurrent, dstData:=ADR(tmpLint));
			eLastType := E_MpType.MP_DINT; // Store as DINT (will truncate)
			IF tmpLint > 2147483647 THEN // MAX_DINT
				diLastDINT := 2147483647;
			ELSIF tmpLint < -2147483648 THEN // MIN_DINT
				diLastDINT := -2147483648;
			ELSE
				diLastDINT := LINT_TO_DINT(tmpLint);
			END_IF;
			pCurrent := pCurrent + 8;
		
		// --- Floating-Point Numbers ---
		16#CA: // float 32
			eLastType := E_MpType.MP_REAL;
			MEMUtils.MemCpy(pbyDest:=ADR(rTemp), pbySrc:=pCurrent, dwSize:=SIZEOF(rTemp));
			Swap32To(ADR(rTemp), ADR(rLastREAL));
			pCurrent := pCurrent + 4;
			
		16#CB: // float 64
			Swap64To(srcData:=pCurrent, dstData:=ADR(tmpLReal));
			eLastType := E_MpType.MP_REAL; // Convert to REAL (may lose precision)
			rLastREAL := LREAL_TO_REAL(tmpLReal);
			pCurrent := pCurrent + 8;
		
		// --- String Types (Fix 3: clamp length against remaining buffer before MemCpy) ---
		16#D9: // str 8
			udiLength := pCurrent^;
			pCurrent := pCurrent + 1;
			eLastType := E_MpType.MP_STRING;
			IF udiLength > 255 THEN udiLength := 255; END_IF;
			IF (pCurrent + udiLength) > (pStart + udiSize) THEN
				udiLength := DWORD_TO_UDINT((pStart + udiSize) - pCurrent);
			END_IF;
			MEMUtils.MemCpy(pbyDest:=ADR(sLastString), pbySrc:=pCurrent, dwSize:=UDINT_TO_DWORD(udiLength));
			sLastString[udiLength] := 0;
			pCurrent := pCurrent + udiLength;

		16#DA: // str 16
			pWordLen := pCurrent;
			Swap16To(pWordLen, ADR(udiLength));
			pCurrent := pCurrent + 2;
			eLastType := E_MpType.MP_STRING;
			IF udiLength > 255 THEN udiLength := 255; END_IF;
			IF (pCurrent + udiLength) > (pStart + udiSize) THEN
				udiLength := DWORD_TO_UDINT((pStart + udiSize) - pCurrent);
			END_IF;
			MEMUtils.MemCpy(pbyDest:=ADR(sLastString), pbySrc:=pCurrent, dwSize:=UDINT_TO_DWORD(udiLength));
			sLastString[udiLength] := 0;
			pCurrent := pCurrent + udiLength;

		16#DB: // str 32
			pDwordLen := pCurrent;
			Swap32To(pDwordLen, ADR(udiLength));
			pCurrent := pCurrent + 4;
			eLastType := E_MpType.MP_STRING;
			IF udiLength > 255 THEN udiLength := 255; END_IF;
			IF (pCurrent + udiLength) > (pStart + udiSize) THEN
				udiLength := DWORD_TO_UDINT((pStart + udiSize) - pCurrent);
			END_IF;
			MEMUtils.MemCpy(pbyDest:=ADR(sLastString), pbySrc:=pCurrent, dwSize:=UDINT_TO_DWORD(udiLength));
			sLastString[udiLength] := 0;
			pCurrent := pCurrent + udiLength;
		
		// --- Binary Types (Fix 2: prevent desync when bin appears in a stream) ---
		16#C4: // bin 8
			udiLength := pCurrent^;
			pCurrent := pCurrent + 1 + udiLength;
			eLastType := E_MpType.MP_BINARY;

		16#C5: // bin 16
			pWordLen := pCurrent;
			Swap16To(pWordLen, ADR(udiLength));
			pCurrent := pCurrent + 2 + udiLength;
			eLastType := E_MpType.MP_BINARY;

		16#C6: // bin 32
			pDwordLen := pCurrent;
			Swap32To(pDwordLen, ADR(udiLength));
			pCurrent := pCurrent + 4 + udiLength;
			eLastType := E_MpType.MP_BINARY;

		// --- Ext Types (skip-over; preserves alignment for downstream iteration) ---
		16#D4: pCurrent := pCurrent + 2;  eLastType := E_MpType.MP_BINARY; // fixext 1  (type+1)
		16#D5: pCurrent := pCurrent + 3;  eLastType := E_MpType.MP_BINARY; // fixext 2  (type+2)
		16#D6: pCurrent := pCurrent + 5;  eLastType := E_MpType.MP_BINARY; // fixext 4  (type+4)
		16#D7: pCurrent := pCurrent + 9;  eLastType := E_MpType.MP_BINARY; // fixext 8  (type+8)
		16#D8: pCurrent := pCurrent + 17; eLastType := E_MpType.MP_BINARY; // fixext 16 (type+16)

		16#C7: // ext 8  : [len:1][type:1][data:len]
			udiLength := pCurrent^;
			pCurrent := pCurrent + 2 + udiLength;
			eLastType := E_MpType.MP_BINARY;

		16#C8: // ext 16 : [len:2][type:1][data:len]
			pWordLen := pCurrent;
			Swap16To(pWordLen, ADR(udiLength));
			pCurrent := pCurrent + 3 + udiLength;
			eLastType := E_MpType.MP_BINARY;

		16#C9: // ext 32 : [len:4][type:1][data:len]
			pDwordLen := pCurrent;
			Swap32To(pDwordLen, ADR(udiLength));
			pCurrent := pCurrent + 5 + udiLength;
			eLastType := E_MpType.MP_BINARY;

		// --- Array Types ---
		16#DC: // array 16
			pWordLen := pCurrent;
			Swap16To(pWordLen, ADR(uiLastCount));
			pCurrent := pCurrent + 2;
			eLastType := E_MpType.MP_ARRAY;
			
		16#DD: // array 32
			pDwordLen := pCurrent;
			Swap32To(pDwordLen, ADR(udiCount32));
			pCurrent := pCurrent + 4;
			eLastType := E_MpType.MP_ARRAY;
			// Store in uiLastCount (may truncate if > 65535)
			IF udiCount32 > 65535 THEN
				uiLastCount := 65535;
			ELSE
				uiLastCount := UDINT_TO_UINT(udiCount32);
			END_IF;
		
		// --- Map Types ---
		16#DE: // map 16
			pWordLen := pCurrent;
			Swap16To(pWordLen, ADR(uiLastCount));
			pCurrent := pCurrent + 2;
			eLastType := E_MpType.MP_MAP;
			
		16#DF: // map 32
			pDwordLen := pCurrent;
			Swap32To(pDwordLen, ADR(udiCount32));
			pCurrent := pCurrent + 4;
			eLastType := E_MpType.MP_MAP;
			// Store in uiLastCount (may truncate if > 65535)
			IF udiCount32 > 65535 THEN
				uiLastCount := 65535;
			ELSE
				uiLastCount := UDINT_TO_UINT(udiCount32);
			END_IF;
			
	ELSE
		// --- Fixint Types (handled in ELSE to catch ranges) ---
		IF (byMarker >= 16#A0 AND byMarker <= 16#BF) THEN // fixstr
			udiLength := byMarker - 16#A0;
			eLastType := E_MpType.MP_STRING;
			IF (pCurrent + udiLength) > (pStart + udiSize) THEN
				udiLength := DWORD_TO_UDINT((pStart + udiSize) - pCurrent);
			END_IF;
			MEMUtils.MemCpy(pbyDest:=ADR(sLastString), pbySrc:=pCurrent, dwSize:=UDINT_TO_DWORD(udiLength));
			sLastString[udiLength] := 0;
			pCurrent := pCurrent + udiLength;
			
		ELSIF (byMarker >= 16#80 AND byMarker <= 16#8F) THEN // fixmap
			eLastType := E_MpType.MP_MAP;
			uiLastCount := byMarker - 16#80;
			
		ELSIF (byMarker >= 16#90 AND byMarker <= 16#9F) THEN // fixarray
			eLastType := E_MpType.MP_ARRAY;
			uiLastCount := byMarker - 16#90;
			
		ELSIF (byMarker <= 16#7F) THEN // positive fixint
			eLastType := E_MpType.MP_DINT;
			diLastDINT := BYTE_TO_DINT(byMarker);
			
		ELSIF (byMarker >= 16#E0) THEN // negative fixint (0xE0-0xFF)
			eLastType := E_MpType.MP_DINT;
			tmpSint := BYTE_TO_SINT(byMarker);
			diLastDINT := SINT_TO_DINT(tmpSint);
		END_IF;
	END_CASE;

	UnpackNext := eLastType;

