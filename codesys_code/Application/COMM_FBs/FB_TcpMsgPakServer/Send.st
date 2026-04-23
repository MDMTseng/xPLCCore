METHOD Send : BOOL
VAR_INPUT
	BUFFER_PTR              : POINTER TO BYTE;
	BUFFER_LEN              : UDINT;
END_VAR
VAR

    eServerTxErrorID : NBS.ERROR;
END_VAR
(* =========== IMPLEMENTATION =========== *)

Send := FALSE;
IF (CLIENT.xActive = FALSE) THEN
	RETURN;
END_IF

eServerTxErrorID := CLIENT.Write((BUFFER_PTR), BUFFER_LEN);

IF (eServerTxErrorID = TCP.ERROR.NO_ERROR) THEN
	Send := TRUE;
END_IF
