{attribute 'qualified_only'}
{attribute 'strict'}
TYPE FlyEventTriggerType :
(
	NOP := 0,
	// held for a TAPE_CYCLE between the command and arming its shots;
	// not processed and not counted as free
	Reserved := 1,
	MovmentProgressTrigger := 20,
	SequenceDelayWait := 25,

	DistanceTrigger := 120,
	PulseTrigger := 130
);
END_TYPE
