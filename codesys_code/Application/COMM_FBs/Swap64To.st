FUNCTION Swap64To : BOOL
VAR_INPUT
	srcData : POINTER TO BYTE; // Pointer to the source 8-byte data
	dstData : POINTER TO BYTE; // Pointer to the destination 8-byte data
END_VAR
VAR
END_VAR
(* =========== IMPLEMENTATION =========== *)

	IF dstData = 0 OR srcData = 0 THEN
		Swap64To := FALSE;
		RETURN;
	END_IF;

	// srcData (big-endian): [0][1][2][3][4][5][6][7]
	// dstData (little-endian): [7][6][5][4][3][2][1][0]
	dstData[0] := srcData[7];
	dstData[1] := srcData[6];
	dstData[2] := srcData[5];
	dstData[3] := srcData[4];
	dstData[4] := srcData[3];
	dstData[5] := srcData[2];
	dstData[6] := srcData[1];
	dstData[7] := srcData[0];

	Swap64To := TRUE;