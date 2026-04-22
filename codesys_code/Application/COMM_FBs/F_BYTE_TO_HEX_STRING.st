(*
FUNCTION F_BYTE_TO_HEX_STRING
Description: Converts a single BYTE value to its two-character hexadecimal STRING representation.
             E.g., 16#0A -> '0A', 16#FF -> 'FF'
*)
FUNCTION F_BYTE_TO_HEX_STRING : STRING(2)
VAR_INPUT
	byValue : BYTE; // The byte value to convert
END_VAR
VAR CONSTANT
    HEX_CHARS : ARRAY[0..15] OF STRING(1) := ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9', 'A', 'B', 'C', 'D', 'E', 'F'];
END_VAR
VAR
	byHighNibble: BYTE;
	byLowNibble : BYTE;
END_VAR
(* =========== IMPLEMENTATION =========== *)

	
	// Get the high nibble (upper 4 bits)
	byHighNibble := SHR(byValue, 4) AND 16#0F;
	// Get the low nibble (lower 4 bits)
	byLowNibble := byValue AND 16#0F;

	// Use array lookup for hexadecimal characters and concatenate them
	F_BYTE_TO_HEX_STRING := CONCAT(HEX_CHARS[BYTE_TO_INT(byHighNibble)], HEX_CHARS[BYTE_TO_INT(byLowNibble)]);
