METHOD PackDINT : POINTER TO BYTE
VAR_INPUT
	pStart : POINTER TO BYTE;
	diValue : DINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)

    pStart^ := 16#D2;//header for int_64
	Swap32To(ADR(diValue),pStart+1);
    PackDINT := pStart + 1+4;