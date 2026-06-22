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

// 2026-06-23 code-review note: NBS.TCP_Connection.Write returns an
// ERROR enum but no bytes-sent output. We trust that NO_ERROR means
// "queued for send to the OS socket buffer" and rely on NBS / kernel
// backpressure to drop the connection on real send failure (eServerTx
// will return a non-NO_ERROR code on the NEXT call).
// If partial-send becomes a real concern (e.g. on multi-KB replies
// under heavy backpressure) we'd need an external watchdog
// (LastSendStartMs vs ack timeout) since the NBS API doesn't expose
// the actual byte count.
eServerTxErrorID := CLIENT.Write((BUFFER_PTR), BUFFER_LEN);

IF (eServerTxErrorID = TCP.ERROR.NO_ERROR) THEN
	Send := TRUE;
END_IF
