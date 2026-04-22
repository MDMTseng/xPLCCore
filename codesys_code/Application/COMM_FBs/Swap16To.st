FUNCTION Swap16To : BOOL // Or VOID if your CoDeSys version supports it for functions
VAR_INPUT
	srcData : POINTER TO BYTE;
	dstData : POINTER TO BYTE;
END_VAR
VAR
END_VAR
(* =========== IMPLEMENTATION =========== *)

	// Check for a valid pointer (optional but good practice)
	IF dstData = 0 OR srcData=0 THEN
		Swap16To := FALSE;
		RETURN;
	END_IF;
	
	
	dstData[0]:=srcData[1];
	dstData[1]:=srcData[0];

	Swap16To := TRUE; // Indicate successful swap