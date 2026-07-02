(* =========== IMPLEMENTATION =========== *)
AxisGroupManagerFb.Update();

GroupReadStatusFb(
    AxisGroup := AxisGroupManagerFb._agAxisGroup,
    Enable := 1,
    ActiveMovementId =>
);


GroupReadPositionFb(
    AxisGroup := AxisGroupManagerFb._agAxisGroup,
    Enable := TRUE
);


GroupActualPositionFb(
    AxisGroup := AxisGroupManagerFb._agAxisGroup,
    Enable := 1,
    CoordSystem := SMC_COORD_SYSTEM.WCS,
    Position => ReadPosition,
);

// B2c fix (analysis 1.6): .Valid/.Error were never checked, so a stale
// or garbage ReadPosition fed the DistanceTrigger compare (bit us live:
// a freshly downloaded app reads TCP=(0,0,0) -- commit 74a01c2). Latch
// the health flag here; ProcessFlyEventsAndIo gates DistanceTrigger
// evaluation on it. Deliberately NOT an EV_ERROR source: a transient
// invalid read must not kill the machine.
ArmPositionValid := GroupActualPositionFb.Valid
                    AND NOT GroupActualPositionFb.Error;
