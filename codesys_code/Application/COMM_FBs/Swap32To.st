FUNCTION Swap32To : BOOL // Or VOID if your CoDeSys version supports it for functions
VAR_INPUT
	srcData : POINTER TO BYTE;
	dstData : POINTER TO BYTE;
END_VAR
VAR
END_VAR
(* =========== IMPLEMENTATION =========== *)

	// Check for a valid pointer (optional but good practice)
	IF dstData = 0 OR srcData=0 THEN
		Swap32To := FALSE;
		RETURN;
	END_IF;
	
	
	dstData[0]:=srcData[3];
	dstData[3]:=srcData[0];
	
	
	dstData[1]:=srcData[2];
	dstData[2]:=srcData[1];

	Swap32To := TRUE; // Indicate successful swap