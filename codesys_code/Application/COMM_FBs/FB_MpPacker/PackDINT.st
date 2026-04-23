METHOD PackDINT : POINTER TO BYTE
VAR_INPUT
	pStart : POINTER TO BYTE;
	diValue : DINT;
END_VAR
VAR
	tmpInt : INT;
	pNext : POINTER TO BYTE;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Compact signed encoding. Reader already accepts every marker below via
// FB_MpUnpacker.TryReadINT64 / UnpackNext, so shrinking the wire form is a
// pure bandwidth win with no reader-side changes required.

IF diValue >= 0 AND diValue <= 127 THEN
	// positive fixint (0x00-0x7F): 1 byte
	pStart^ := DINT_TO_BYTE(diValue);
	PackDINT := pStart + 1;

ELSIF diValue >= -32 AND diValue < 0 THEN
	// negative fixint (0xE0-0xFF): 1 byte, two's-complement low 8 bits
	pStart^ := DINT_TO_BYTE(diValue);
	PackDINT := pStart + 1;

ELSIF diValue >= -128 AND diValue <= 127 THEN
	// int 8 (0xD0): 2 bytes
	pStart^ := 16#D0;
	pNext := pStart + 1;
	pNext^ := DINT_TO_BYTE(diValue);
	PackDINT := pStart + 2;

ELSIF diValue >= -32768 AND diValue <= 32767 THEN
	// int 16 (0xD1): 3 bytes, big-endian
	pStart^ := 16#D1;
	tmpInt := DINT_TO_INT(diValue);
	Swap16To(ADR(tmpInt), pStart + 1);
	PackDINT := pStart + 3;

ELSE
	// int 32 (0xD2): 5 bytes, big-endian
	pStart^ := 16#D2;
	Swap32To(ADR(diValue), pStart + 1);
	PackDINT := pStart + 5;

END_IF;
