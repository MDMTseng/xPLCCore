TYPE FlyEventData :
STRUCT
	(* Source metadata ------------------------------------------------------ *)
	SourceCommandId : DINT; (* Command that created this fly event. *)

	(* Trigger configuration ------------------------------------------------ *)
	TriggerType : FlyEventTriggerType;

	(* trigger by movement progress ---------------------------------------- *)
	TargetMovementId : SMC_Movement_Id;
	TargetMovementProgress : LREAL;

	(* trigger by distance ------------------------------------------------- *)
	TargetPos_X: LREAL;
	TargetPos_Y: LREAL;
	TargetPos_Z: LREAL;
	TargetDistance: LREAL;
	TriggerInBoundary: BOOL;

	(* trigger by conveyor pulse threshold (PulseTrigger).
	   Fires once GVL.ConveyorPulseRaw >= TargetPulseValue. Caller supplies
	   an ABSOLUTE pulse target (typical pattern: snapshot
	   GVL.ConveyorPulseRaw at M4-issue time and add the desired pulse
	   offset host-side, mirroring how DistanceTrigger takes an absolute
	   WCS point). Single-shot, forward-only -- the >= test makes any
	   future pulse >= target also fire, so callers in retrograde or
	   reset-counter scenarios should clear FlyEvents first. *)
	TargetPulseValue: DINT;




	TimeToLiveMs : DINT; (* Optional time (ms) before the trigger auto-expires. -1 = no expiry. *)




	(* Action payload ------------------------------------------------------- *)
	EventId : DINT;
	ActionType : FlyEventActType;

	PinGrp : INT; (* Byte index in the digital output array. *)
	ResetDelayMs : INT; (* Delay (ms) before the reset stage fires. *)
	IoStageCount : INT; (* Number of sequential IO stages configured. *)
	IoStageIndex : INT; (* Next stage index to execute. *)
	IoStageDelayCountdown : DINT; (* Countdown before the next stage fires. *)
	IoStageDelay : ARRAY [0..8] OF DINT; (* Delay (ms) for each stage before it fires. *)
	IoStagePinMask : ARRAY [0..8] OF ULINT; (* Pin mask per stage. *)
	IoStagePinState : ARRAY [0..8] OF ULINT; (* Desired pin state per stage. *)
END_STRUCT
END_TYPE
