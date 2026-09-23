PROGRAM PRG_EcatEsp
VAR
    byOut0          : USINT;        // bit 0 -> ESP32 LED
    byOut1          : USINT := 16#5A; // echoed back on in1
    byIn0           : USINT;        // ESP32 counter
    byIn1           : USINT;        // echo of out1
    byLastIn0       : USINT;
    udiCounterSeen  : UDINT;        // cycles the counter moved
    xRoundTripOk    : BOOL;
END_VAR
(* =========== IMPLEMENTATION =========== *)
ecat_esp_out0 := byOut0;
ecat_esp_out1 := byOut1;
byIn0 := ecat_esp_in0;
byIn1 := ecat_esp_in1;

IF byIn0 <> byLastIn0 THEN
    udiCounterSeen := udiCounterSeen + 1;
END_IF
byLastIn0 := byIn0;
xRoundTripOk := (byIn1 = byOut1);
