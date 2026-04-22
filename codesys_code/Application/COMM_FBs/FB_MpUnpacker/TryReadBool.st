METHOD TryReadBool : BOOL
VAR_INPUT
	sPath 	: STRING;
	default : BOOL;
END_VAR
VAR
	pValue : POINTER TO BYTE;
END_VAR
(* =========== IMPLEMENTATION =========== *)
	TryReadBool := default;
	
	IF FindValueByPath(sPath, pValue) THEN
		IF pValue^ = 16#C2 THEN
			TryReadBool := FALSE;
		ELSIF pValue^ = 16#C3 THEN
			TryReadBool := TRUE;
		END_IF;
	END_IF;
