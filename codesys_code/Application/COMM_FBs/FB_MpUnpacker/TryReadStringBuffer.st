METHOD TryReadStringBuffer : POINTER TO BYTE
VAR_INPUT
	sPath 		: STRING;
	pStringLength : POINTER TO UDINT; // Pointer to store the extracted string length
END_VAR
VAR
	pItemStart : POINTER TO BYTE;    // Pointer to the start of the MessagePack item (type byte)
	tmpLength  : UDINT;              // Temporary variable for 2-byte or 4-byte string lengths
END_VAR
(* =========== IMPLEMENTATION =========== *)


	TryReadStringBuffer := 0; // Initialize return pointer to NULL
	
	// Always check if the output pointer is valid before dereferencing
	IF (pStringLength = 0) THEN
		RETURN;
	END_IF;
	
	// Initialize the output length to 0
	pStringLength^ := 0;

	// Attempt to find the value by path
	IF FindValueByPath(sPath, pItemStart) THEN
		// Check for NULL pointer from FindValueByPath (double-check, though 'IF NOT' covers most cases)
		IF pItemStart = 0 THEN
			RETURN; // Value not found or invalid path
		END_IF;

		// Determine string type based on the first byte (type byte)
		CASE pItemStart^ OF
			// --- Fixstr (0xA0 - 0xBF) ---
			16#A0..16#BF: // 101xxxxx: Fixstr (length is in the lower 5 bits of the type byte)
				pStringLength^ := BYTE_TO_UDINT(pItemStart^ - 16#A0); // Extract length from the type byte
				TryReadStringBuffer := pItemStart + 1; // Content starts after the type byte

			// --- str 8 (0xD9) ---
			16#D9: // 0xD9: str 8 (length is 1 byte)
				pStringLength^ := BYTE_TO_UDINT(pItemStart[1]); // Length is the byte immediately following the type byte
				TryReadStringBuffer := pItemStart + 2; // Content starts after type byte + length byte

			// --- str 16 (0xDA) ---
			16#DA: // 0xDA: str 16 (length is 2 bytes)
				// Swap the 2-byte length from big-endian (MessagePack) to little-endian (CoDeSys)
				Swap16To(srcData:=ADR(pItemStart[1]), dstData:=ADR(tmpLength)); // Swap into temp UDINT
				pStringLength^ := tmpLength; // Assign the (now correctly endian) value
				TryReadStringBuffer := pItemStart + 3; // Content starts after type byte + 2 length bytes

			// --- str 32 (0xDB) ---
			16#DB: // 0xDB: str 32 (length is 4 bytes)
				// Swap the 4-byte length from big-endian (MessagePack) to little-endian (CoDeSys)
				Swap32To(srcData:=ADR(pItemStart[1]), dstData:=ADR(tmpLength)); // Use Swap32To for 4 bytes
				pStringLength^ := tmpLength;
				TryReadStringBuffer := pItemStart + 5; // Content starts after type byte + 4 length bytes

			ELSE
				// If the type byte is not a MessagePack string format,
				// return NULL pointer and 0 length (already initialized at the beginning).
				TryReadStringBuffer := 0;
		END_CASE;
	END_IF;