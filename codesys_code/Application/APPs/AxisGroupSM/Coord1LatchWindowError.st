METHOD Coord1LatchWindowError : BOOL
VAR_INPUT
    // Host correlation id echoed in the COORD1_ERROR payload.
    eventId : DINT;
    // Reference pulse of the bind the fault relates to. 0 = "bind never
    // happened, no ref" (fly-bind refusals fault before any commit).
    refPulse : DINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// B3 fix (analysis 2.1): the COORD1 error-context snapshot used to live
// TWICE -- in the window-exit detector (AxisGroupSM main body) and in
// the fly-bind fault branches (ProcessFlyEventsAndIo) -- with partially
// different field sets. One latch for all fault sources; the
// COORD1_ERROR emitter in the main body consumes Coord1ErrPending and
// pushes the event (plus auto-unbind) exactly as before. Callers set
// GVL.LastErrorSource and call AxisGroupManagerFb.Transition(EV_ERROR)
// themselves so the diagnostic source string stays per-path.
Coord1LatchWindowError := TRUE;

GVL.Coord1ErrPending      := TRUE;
GVL.Coord1ErrAtMovementId := CurrentMovementId;
GVL.Coord1ErrAtProgress   := CurrentMovementIdProgress;
GVL.Coord1ErrAtPulse      := GVL.ConveyorPulseRaw;
GVL.Coord1ErrAtArmX       := ReadPosition.c.x;
GVL.Coord1ErrAtArmY       := ReadPosition.c.y;
GVL.Coord1ErrAtArmZ       := ReadPosition.c.z;
GVL.Coord1ErrAtPcsX       := Pcs1KernelTransform.X;
GVL.Coord1ErrAtPcsY       := Pcs1KernelTransform.Y;
GVL.Coord1ErrAtPcsZ       := Pcs1KernelTransform.Z;
GVL.Coord1ErrRefPulse     := refPulse;
GVL.Coord1ErrExitPulse    := GVL.Coord1ExitPulse;
GVL.Coord1BindEventId     := eventId;
