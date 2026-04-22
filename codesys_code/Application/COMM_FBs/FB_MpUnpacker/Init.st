
(* Method: Init - (Implementation is unchanged) *)
METHOD Init : BOOL
VAR_INPUT
	pData 		: POINTER TO BYTE;
	udiDataSize : UDINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
	IF pData = 0 OR udiDataSize = 0 THEN Init := FALSE; RETURN; END_IF;
	pStart 		:= pData;
	pCurrent 	:= pData;
	udiSize 	:= udiDataSize;
	Init 		:= TRUE;