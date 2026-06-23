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

// NBS.TCP_Connection.Write returns an ERROR enum but no bytes-sent
// output. NO_ERROR means "queued for send to the OS socket buffer";
// partial-send under heavy backpressure isn't observable here.
eServerTxErrorID := CLIENT.Write((BUFFER_PTR), BUFFER_LEN);

IF (eServerTxErrorID = TCP.ERROR.NO_ERROR) THEN
	Send := TRUE;
ELSE
	// Hard write error -> socket is unhealthy; reset immediately,
	// symmetric with the Read-error path at FB top-level. Pre-fix this
	// only returned Send=FALSE and waited for the TX_STALL_DROP_THRESHOLD
	// escalation (~500ms) before reclaiming the socket; that's an order
	// of magnitude longer than necessary when Write itself already told
	// us the socket is dead. Guard the counter bump so a single error
	// observed across multiple Send calls during the same reset cycle
	// doesn't multi-count.
	IF NOT xResetConnection THEN
		GVL.WriteErrorResetCount := GVL.WriteErrorResetCount + 1;
	END_IF
	xResetConnection := TRUE;
END_IF
