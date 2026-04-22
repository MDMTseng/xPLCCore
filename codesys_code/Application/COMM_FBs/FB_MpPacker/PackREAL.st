METHOD PackREAL : POINTER TO BYTE
VAR_INPUT
	pStart : POINTER TO BYTE;
	rValue : REAL;
END_VAR
VAR
	rSwappedValue : REAL;
END_VAR
(* =========== IMPLEMENTATION =========== *)

	
    pStart^ := 16#CA;//header for real 32bit
	Swap32To(ADR(rValue),pStart+1);
    PackREAL := pStart + 1+4;