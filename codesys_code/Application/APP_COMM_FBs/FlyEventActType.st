

{attribute 'qualified_only'}
{attribute 'strict'}
TYPE FlyEventActType :
(
	NOP := 0,
	PIN_OPERATION := 1,
	(* Pin-reset used to be a distinct action code 2001. Replaced first
	   by an auto-appended IoStage built from `ResetDelayMs` (now also
	   removed), and finally by callers supplying the reset stage
	   directly in pin_op_seq. The 2001 gap is left for wire-format
	   stability so older consumers don't get an unexpected re-purposed
	   code. *)


	ACK_SRC_ID := 2002,

	(* Phase 4 step 3: scheduled COORD1_BIND. When the FlyEvent fires
	   (typically via PulseTrigger at the conveyor pulse the host wants
	   the bind to anchor at), the action handler atomically applies the
	   bind state stored in the FlyEventData bind-* fields. Lets the host
	   pre-schedule bind by belt position instead of racing a SYS BIND
	   against in-flight motion. *)
	COORD1_BIND := 3001,

	RETURN_ACT := 2342
);
END_TYPE
