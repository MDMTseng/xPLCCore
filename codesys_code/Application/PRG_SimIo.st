PROGRAM PRG_SimIo
VAR
    udiSide     : UDINT;    // CAM_Side (pin 8) rising edges
    udiBtm      : UDINT;    // CAM_Btm (pin 10)
    udiFeeder   : UDINT;    // CAM_FlexFeeder (pin 12)
    udiTop      : UDINT;    // CAM_Top (pin 14)
    udiReelAdv  : UDINT;    // ReelAdv (pin 6)
    rBtmX       : REAL;     // arm X/Y at the last CAM_Btm edge, mm
    rBtmY       : REAL;
    uliPrev     : ULINT;
    uliRise     : ULINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
uliRise := AxisGroupSM.DigitalOutputBits AND NOT uliPrev;
uliPrev := AxisGroupSM.DigitalOutputBits;

IF (uliRise AND 16#0100) <> 0 THEN udiSide := udiSide + 1; END_IF
IF (uliRise AND 16#0400) <> 0 THEN
    udiBtm := udiBtm + 1;
    rBtmX := LREAL_TO_REAL(AxisGroupSM.GroupActualPositionFb.Position.c.X);
    rBtmY := LREAL_TO_REAL(AxisGroupSM.GroupActualPositionFb.Position.c.Y);
END_IF
IF (uliRise AND 16#1000) <> 0 THEN udiFeeder := udiFeeder + 1; END_IF
IF (uliRise AND 16#4000) <> 0 THEN udiTop := udiTop + 1; END_IF
IF (uliRise AND 16#0040) <> 0 THEN udiReelAdv := udiReelAdv + 1; END_IF
