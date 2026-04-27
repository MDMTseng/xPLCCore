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
