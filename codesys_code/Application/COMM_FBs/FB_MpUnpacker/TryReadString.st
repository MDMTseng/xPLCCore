METHOD TryReadString : STRING
VAR_INPUT
	sPath 		: STRING;
	default 		: STRING;
END_VAR
VAR
	buffer : POINTER TO BYTE;
	buffer_size:UDINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
	TryReadString := default;
	
	
	
	buffer:=0;
	buffer:=TryReadStringBuffer(sPath,ADR(buffer_size));
	IF(buffer_size>SIZEOF(TryReadString)-1)THEN//and '\0'
		RETURN;
	END_IF
	MEMUtils.MemCpy(ADR(TryReadString),buffer,UDINT_TO_UINT(buffer_size));
	TryReadString[buffer_size]:=0;
	//isOK:=TryReadStringPtr(sPath,ADR(TryReadString),SIZEOF(TryReadString));