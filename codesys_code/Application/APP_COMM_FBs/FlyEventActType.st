

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

	RETURN_ACT := 2342
);
END_TYPE
