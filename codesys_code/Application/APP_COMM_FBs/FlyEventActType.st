

{attribute 'qualified_only'}
{attribute 'strict'}
TYPE FlyEventActType :
(
	NOP := 0,
	PIN_OPERATION := 1,
	(* Pin-reset is now expanded into a follow-up stage in ProcessMotionPacket.st
	   (see ResetDelayMs handling). Kept the enum gap (no value 2001) so
	   wire-format consumers don't get an unexpected re-purposed code. *)


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
