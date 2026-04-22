METHOD TryReadINT64 : LINT
VAR_INPUT
	sPath 	: STRING; 				// Path to the value
	default 	: LINT; 		// Default value if not found or cannot decode
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
	tmpLint 	: LINT; // For int 64
	tmpULint 	: ULINT; // For uint 64
	// tmpReal : REAL; // Not needed as floats are excluded
	// tmpLReal : LREAL; // Not needed as floats are excluded
	
	// Define MAX_LINT if not globally available
	cMAX_LINT : LINT := 9223372036854775807; // 2^63 - 1
END_VAR
(* =========== IMPLEMENTATION =========== *)


	TryReadINT64 := default; // Initialize with default value

	IF FindValueByPath(sPath, pValue) THEN
		pCurrent := pValue; // Start at the beginning of the value's data

		// Get the MessagePack type byte
		tmpByte := pCurrent^;
		pCurrent := pCurrent + 1; // Advance pointer past the type byte

		CASE tmpByte OF
			// --- Positive Fixint (0x00 - 0x7F) ---
			0..16#7F: // 0xxxxxxx: Positive Fixint (1 byte)
				TryReadINT64 := BYTE_TO_LINT(tmpByte);

			// --- Negative Fixint (0xE0 - 0xFF) ---
			16#E0..16#FF: // 111xxxxx: Negative Fixint (-32 to -1) (1 byte)
				tmpSint := BYTE_TO_SINT(tmpByte);
				TryReadINT64 := SINT_TO_LINT(tmpSint);

			// --- Unsigned Integers ---
			16#CC: // uint 8 (1 byte value)
				tmpByte := pCurrent^;
				TryReadINT64 := BYTE_TO_LINT(tmpByte);

			16#CD: // uint 16 (2 byte value)
				Swap16To(srcData:=pCurrent, dstData:=ADR(tmpWord));
				TryReadINT64 := WORD_TO_LINT(tmpWord);

			16#CE: // uint 32 (4 byte value)
				Swap32To(srcData:=pCurrent, dstData:=ADR(tmpUdint));
				TryReadINT64 := UDINT_TO_LINT(tmpUdint);

			16#CF: // uint 64 (8 byte value)
				Swap64To(srcData:=pCurrent, dstData:=ADR(tmpULint));
				// Rule: "except (UINT64 && top bit is 1)"
				// This means if tmpULint > MAX_LINT, it cannot be represented as LINT.
				IF tmpULint > cMAX_LINT THEN
					TryReadINT64 := default; // Value too large for signed 64-bit integer
				ELSE
					TryReadINT64 := ULINT_TO_LINT(tmpULint);
				END_IF;

			// --- Signed Integers ---
			16#D0: // int 8 (1 byte value)
				tmpSint := pCurrent^;
				TryReadINT64 := SINT_TO_LINT(tmpSint);

			16#D1: // int 16 (2 byte value)
				Swap16To(srcData:=pCurrent, dstData:=ADR(tmpInt));
				TryReadINT64 := INT_TO_LINT(tmpInt);

			16#D2: // int 32 (4 byte value)
				Swap32To(srcData:=pCurrent, dstData:=ADR(tmpDint));
				TryReadINT64 := DINT_TO_LINT(tmpDint);

			16#D3: // int 64 (8 byte value)
				Swap64To(srcData:=pCurrent, dstData:=ADR(tmpLint));
				TryReadINT64 := tmpLint; // It's already LINT

			// --- Floating-Point Numbers ---
			16#CA, // float 32
			16#CB: // float 64
				// Rule: "they will not read floating point number"
				TryReadINT64 := default; // Explicitly return default for floats

			ELSE
				// Handle other unsupported MessagePack types (string, array, map, nil, ext type)
				TryReadINT64 := default;
		END_CASE;
	END_IF;