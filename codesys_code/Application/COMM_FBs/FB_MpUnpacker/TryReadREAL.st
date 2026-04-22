METHOD TryReadREAL : REAL
VAR_INPUT
	sPath 	: STRING; 				// Path to the value
	default 	: REAL; 		// Default value if not found or cannot decode
END_VAR
VAR
	pValue 	: POINTER TO BYTE;
	pCurrent : POINTER TO BYTE; // Pointer to advance through the data
	tmpByte 	: BYTE;
	tmpSint 	: SINT; // For int 8
	tmpWord 	: WORD; // For uint 16, int 16
	tmpInt 	: INT;  // For int 16
	tmpDint 	: DINT; // For uint 32, int 32
	tmpUdint 	: UDINT; // For uint 32
	tmpLint 	: LINT; // For uint 64, int 64
	tmpULint 	: ULINT; // For uint 64
	tmpReal 	: REAL; // For float 32
	tmpLReal 	: LREAL; // For float 64 (double precision)
END_VAR
(* =========== IMPLEMENTATION =========== *)


	TryReadREAL := default; // Initialize with default value

	IF FindValueByPath(sPath, pValue) THEN
		pCurrent := pValue; // Start at the beginning of the value's data

		// Get the MessagePack type byte
		tmpByte := pCurrent^;
		pCurrent := pCurrent + 1; // Advance pointer past the type byte

		// Use a CASE statement for clarity and extensiveness
		CASE tmpByte OF
			// --- Positive Fixint (0x00 - 0x7F) ---
			0..16#7F: // 0xxxxxxx: Positive Fixint (1 byte)
				// The value is directly in tmpByte (pCurrent-1, as pCurrent already advanced)
				TryReadREAL := BYTE_TO_REAL(tmpByte);

			// --- Negative Fixint (0xE0 - 0xFF) ---
			16#E0..16#FF: // 111xxxxx: Negative Fixint (-32 to -1) (1 byte)
				// Treat the byte as SINT (signed 8-bit integer) to get the negative value.
				// tmpByte already holds the value
				tmpSint := BYTE_TO_SINT(tmpByte); // This correctly casts the byte's bits as a signed SINT
				TryReadREAL := SINT_TO_REAL(tmpSint);

			// --- Unsigned Integers ---
			16#CC: // uint 8 (1 byte value)
				tmpByte := pCurrent^; // Read the 1-byte unsigned integer
				TryReadREAL := BYTE_TO_REAL(tmpByte);
				// pCurrent will be implicitly advanced by the next loop iteration or return.

			16#CD: // uint 16 (2 byte value)
				Swap16To(srcData:=pCurrent, dstData:=ADR(tmpWord)); // Swap into tmpWord
				TryReadREAL := WORD_TO_REAL(tmpWord);
				// pCurrent := pCurrent + SIZEOF(WORD); // Already handled implicitly by FindValueByPath or next call

			16#CE: // uint 32 (4 byte value)
				Swap32To(srcData:=pCurrent, dstData:=ADR(tmpUdint)); // Swap into tmpUdint
				TryReadREAL := UDINT_TO_REAL(tmpUdint);
				// pCurrent := pCurrent + SIZEOF(UDINT); // Already handled implicitly

			16#CF: // uint 64 (8 byte value)
				Swap64To(srcData:=pCurrent, dstData:=ADR(tmpULint)); // Swap into tmpULint
				// Conversion from ULINT to REAL might lose precision for very large numbers
				TryReadREAL := ULINT_TO_REAL(tmpULint);
				// pCurrent := pCurrent + SIZEOF(ULINT); // Already handled implicitly

			// --- Signed Integers ---
			16#D0: // int 8 (1 byte value)
				tmpSint := pCurrent^; // Read the 1-byte signed integer
				TryReadREAL := SINT_TO_REAL(tmpSint);
				// pCurrent := pCurrent + SIZEOF(SINT); // Already handled implicitly

			16#D1: // int 16 (2 byte value)
				Swap16To(srcData:=pCurrent, dstData:=ADR(tmpInt)); // Swap into tmpInt
				TryReadREAL := INT_TO_REAL(tmpInt);
				// pCurrent := pCurrent + SIZEOF(INT); // Already handled implicitly

			16#D2: // int 32 (4 byte value)
				Swap32To(srcData:=pCurrent, dstData:=ADR(tmpDint)); // Swap into tmpDint
				TryReadREAL := DINT_TO_REAL(tmpDint);
				// pCurrent := pCurrent + SIZEOF(DINT); // Already handled implicitly

			16#D3: // int 64 (8 byte value)
				Swap64To(srcData:=pCurrent, dstData:=ADR(tmpLint)); // Swap into tmpLint
				// Conversion from LINT to REAL might lose precision for very large numbers
				TryReadREAL := LINT_TO_REAL(tmpLint);
				// pCurrent := pCurrent + SIZEOF(LINT); // Already handled implicitly

			// --- Floating-Point Numbers ---
			16#CA: // float 32 (4 byte value)
				Swap32To(srcData:=pCurrent, dstData:=ADR(tmpReal)); // Swap into tmpReal
				TryReadREAL := tmpReal; // tmpReal now holds the correctly swapped float32
				// pCurrent := pCurrent + SIZEOF(REAL); // Already handled implicitly

			16#CB: // float 64 (8 byte value)
				Swap64To(srcData:=pCurrent, dstData:=ADR(tmpLReal)); // Swap into tmpLReal
				// Convert LREAL to REAL (potential precision loss!)
				TryReadREAL := LREAL_TO_REAL(tmpLReal);
				// pCurrent := pCurrent + SIZEOF(LREAL); // Already handled implicitly

			ELSE
				// Handle unsupported MessagePack types or non-number types gracefully
				// If it's a string, array, map, nil, or ext type, it's not a number and should return the default.
				// Add logging here if needed: 'Unsupported MessagePack type for REAL conversion: %x', tmpByte
				TryReadREAL := default; // Already initialized, but explicitly set for clarity
		END_CASE;
	END_IF;