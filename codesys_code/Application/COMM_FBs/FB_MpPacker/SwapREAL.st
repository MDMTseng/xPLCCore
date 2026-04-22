(*
Helper method for swapping byte order for REAL (Little-Endian to Big-Endian)
*)
METHOD PRIVATE SwapREAL : REAL
VAR_INPUT
	rValue : REAL;
END_VAR
VAR
	rSwapped : REAL;
	prIn : POINTER TO ARRAY[0..3] OF BYTE;
	prOut : POINTER TO ARRAY[0..3] OF BYTE;
END_VAR
(* =========== IMPLEMENTATION =========== *)
	prIn := ADR(rValue);
	prOut := ADR(rSwapped);
	prOut^[0] := prIn^[3];
	prOut^[1] := prIn^[2];
	prOut^[2] := prIn^[1];
	prOut^[3] := prIn^[0];
	SwapREAL := rSwapped;