(*
Method: TryReadObject [NEW FEATURE]
Description: Finds a map or array at a given path and initializes a destination
             unpacker instance to represent that sub-object.
Return Type: BOOL (TRUE on success)
*)
METHOD TryReadObject : BOOL
VAR_INPUT
	sPath 	: STRING; 					// Path to the map or array
	pDest 	: POINTER TO FB_MpUnpacker;	// Pointer to the destination unpacker instance
END_VAR
VAR
	pValue : POINTER TO BYTE;
	udiObjectSize : UDINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
	TryReadObject := FALSE;
	IF pDest = 0 THEN RETURN; END_IF;
	
	IF FindValueByPath(sPath, pValue) THEN
		IF (pValue^ >= 16#80 AND pValue^ <= 16#8F) OR (pValue^ = 16#DE) OR 
		   (pValue^ >= 16#90 AND pValue^ <= 16#9F) OR (pValue^ = 16#DC) THEN
			udiObjectSize := GetValueSize(pObjectStart := pValue);
			
			IF udiObjectSize > 0 THEN
				TryReadObject := pDest^.Init(pData := pValue, udiDataSize := udiObjectSize);
				TryReadObject:=TRUE;
			END_IF;
		END_IF;
	END_IF;