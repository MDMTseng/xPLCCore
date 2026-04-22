FUNCTION TryReadINTBuffer : POINTER TO BYTE
VAR_INPUT
	sPath 	: STRING; 				  // Path to the integer value
	p_data_size : POINTER TO INT;     // Pointer to an INT to store the size (1, 2, 4, or 8 bytes)
	p_signedness : POINTER TO BOOL;   // Pointer to a BOOL to store if it's signed (TRUE) or unsigned (FALSE)
END_VAR
VAR
	pValue 	: POINTER TO BYTE;    // Pointer to the start of the MessagePack value (type byte)
	tmpByte : BYTE;               // To hold the MessagePack type byte
END_VAR
(* =========== IMPLEMENTATION =========== *)

	// Initialize outputs to default/error states
	IF p_data_size <> 0 THEN
		p_data_size^ := 0; // Default size to 0
	END_IF;
	IF p_signedness <> 0 THEN
		p_signedness^ := FALSE; // Default signedness to FALSE (unsigned)
	END_IF;
	TryReadINTBuffer := 0; // Default return to NULL pointer (no integer found/decoded)

	// Attempt to find the value by path
	IF NOT FindValueByPath(sPath, pValue) THEN
		RETURN; // Value not found, return NULL pointer and default output parameters
	END_IF;

	// Check for NULL pointer from FindValueByPath (should be handled by NOT condition above, but good defense)
	IF pValue = 0 THEN
		RETURN;
	END_IF;

	tmpByte := pValue^; // Read the MessagePack type byte

	// Determine integer type, size, and signedness based on the type byte
	CASE tmpByte OF
		// --- Positive Fixint (0x00 - 0x7F) ---
		0..16#7F: // 0xxxxxxx
			IF p_data_size <> 0 THEN p_data_size^ := 1; END_IF;
			IF p_signedness <> 0 THEN p_signedness^ := TRUE; END_IF; // Can be treated as signed
			TryReadINTBuffer := pValue; // Data is the type byte itself

		// --- Negative Fixint (0xE0 - 0xFF) ---
		16#E0..16#FF: // 111xxxxx
			IF p_data_size <> 0 THEN p_data_size^ := 1; END_IF;
			IF p_signedness <> 0 THEN p_signedness^ := TRUE; END_IF;
			TryReadINTBuffer := pValue; // Data is the type byte itself

		// --- Unsigned Integers ---
		16#CC: // uint 8
			IF p_data_size <> 0 THEN p_data_size^ := 1; END_IF;
			IF p_signedness <> 0 THEN p_signedness^ := FALSE; END_IF;
			TryReadINTBuffer := pValue + 1; // Data starts after the type byte

		16#CD: // uint 16
			IF p_data_size <> 0 THEN p_data_size^ := 2; END_IF;
			IF p_signedness <> 0 THEN p_signedness^ := FALSE; END_IF;
			TryReadINTBuffer := pValue + 1;

		16#CE: // uint 32
			IF p_data_size <> 0 THEN p_data_size^ := 4; END_IF;
			IF p_signedness <> 0 THEN p_signedness^ := FALSE; END_IF;
			TryReadINTBuffer := pValue + 1;

		16#CF: // uint 64
			IF p_data_size <> 0 THEN p_data_size^ := 8; END_IF;
			IF p_signedness <> 0 THEN p_signedness^ := FALSE; END_IF;
			TryReadINTBuffer := pValue + 1;

		// --- Signed Integers ---
		16#D0: // int 8
			IF p_data_size <> 0 THEN p_data_size^ := 1; END_IF;
			IF p_signedness <> 0 THEN p_signedness^ := TRUE; END_IF;
			TryReadINTBuffer := pValue + 1;

		16#D1: // int 16
			IF p_data_size <> 0 THEN p_data_size^ := 2; END_IF;
			IF p_signedness <> 0 THEN p_signedness^ := TRUE; END_IF;
			TryReadINTBuffer := pValue + 1;

		16#D2: // int 32
			IF p_data_size <> 0 THEN p_data_size^ := 4; END_IF;
			IF p_signedness <> 0 THEN p_signedness^ := TRUE; END_IF;
			TryReadINTBuffer := pValue + 1;

		16#D3: // int 64
			IF p_data_size <> 0 THEN p_data_size^ := 8; END_IF;
			IF p_signedness <> 0 THEN p_signedness^ := TRUE; END_IF;
			TryReadINTBuffer := pValue + 1;

		ELSE
			// If not an integer type, return NULL pointer and default output parameters
			// (already set at the beginning of the function)
			TryReadINTBuffer := 0;
	END_CASE;