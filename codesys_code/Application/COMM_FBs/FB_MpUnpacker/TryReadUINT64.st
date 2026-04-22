METHOD TryReadUINT64 : ULINT
VAR_INPUT
	sPath 	: STRING; 				// Path to the value
	default 	: ULINT; 		// Default value if not found or cannot decode
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
END_VAR
(* =========== IMPLEMENTATION =========== *)

	TryReadUINT64 := default; // Initialize with default value

	IF FindValueByPath(sPath, pValue) THEN
		pCurrent := pValue; // Start at the beginning of the value's data

		// Get the MessagePack type byte
		tmpByte := pCurrent^;
		pCurrent := pCurrent + 1; // Advance pointer past the type byte

		CASE tmpByte OF
			// --- Positive Fixint (0x00 - 0x7F) ---
			0..16#7F: // 0xxxxxxx: Positive Fixint (1 byte)
				TryReadUINT64 := BYTE_TO_ULINT(tmpByte);

			// --- Negative Fixint (0xE0 - 0xFF) ---
			16#E0..16#FF: // 111xxxxx: Negative Fixint (-32 to -1) (1 byte)
				// Negative numbers cannot be represented as unsigned.
				TryReadUINT64 := default;

			// --- Unsigned Integers ---
			16#CC: // uint 8 (1 byte value)
				tmpByte := pCurrent^;
				TryReadUINT64 := BYTE_TO_ULINT(tmpByte);

			16#CD: // uint 16 (2 byte value)
				Swap16To(srcData:=pCurrent, dstData:=ADR(tmpWord));
				TryReadUINT64 := WORD_TO_ULINT(tmpWord);

			16#CE: // uint 32 (4 byte value)
				Swap32To(srcData:=pCurrent, dstData:=ADR(tmpUdint));
				TryReadUINT64 := UDINT_TO_ULINT(tmpUdint);

			16#CF: // uint 64 (8 byte value)
				Swap64To(srcData:=pCurrent, dstData:=ADR(tmpULint));
				TryReadUINT64 := tmpULint; // It's already ULINT

			// --- Signed Integers ---
			16#D0: // int 8 (1 byte value)
				tmpSint := pCurrent^;
				IF tmpSint < 0 THEN
					TryReadUINT64 := default; // Negative signed int cannot be unsigned
				ELSE
					TryReadUINT64 := SINT_TO_ULINT(tmpSint);
				END_IF;

			16#D1: // int 16 (2 byte value)
				Swap16To(srcData:=pCurrent, dstData:=ADR(tmpInt));
				IF tmpInt < 0 THEN
					TryReadUINT64 := default; // Negative signed int cannot be unsigned
				ELSE
					TryReadUINT64 := INT_TO_ULINT(tmpInt);
				END_IF;

			16#D2: // int 32 (4 byte value)
				Swap32To(srcData:=pCurrent, dstData:=ADR(tmpDint));
				IF tmpDint < 0 THEN
					TryReadUINT64 := default; // Negative signed int cannot be unsigned
				ELSE
					TryReadUINT64 := DINT_TO_ULINT(tmpDint);
				END_IF;

			16#D3: // int 64 (8 byte value)
				Swap64To(srcData:=pCurrent, dstData:=ADR(tmpLint));
				IF tmpLint < 0 THEN
					TryReadUINT64 := default; // Negative signed int cannot be unsigned
				ELSE
					TryReadUINT64 := LINT_TO_ULINT(tmpLint);
				END_IF;

			// --- Floating-Point Numbers ---
			16#CA, // float 32
			16#CB: // float 64
				// Rule: "they will not read floating point number"
				TryReadUINT64 := default; // Explicitly return default for floats

			ELSE
				// Handle other unsupported MessagePack types (string, array, map, nil, ext type)
				TryReadUINT64 := default;
		END_CASE;
	END_IF;