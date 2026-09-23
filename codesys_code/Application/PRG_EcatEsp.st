PROGRAM PRG_EcatEsp
VAR
    byOut0          : USINT;        // bit 0 -> ESP32 LED
    byOut1          : USINT := 16#5A; // echoed back on in1
    byIn0           : USINT;        // ESP32 heartbeat counter
    byIn1           : USINT;        // echo of out1
    byLastIn0       : USINT;
    udiCounterSeen  : UDINT;        // cycles the heartbeat moved
    uiStaleCycles   : UINT;         // cycles since it last moved
    xEspAlive       : BOOL;         // heartbeat moved within 100 cycles
    xRoundTripOk    : BOOL;

    // QY2204-IIC (AS5600) encoder
    uiEncRaw        : UINT;         // 0..4095, one turn
    rEncDeg         : REAL;         // 0..360
    diEncPos        : DINT;         // multi-turn counts, from ESP32 power-up
    rEncTurns       : REAL;         // diEncPos / 4096
    byEncStatus     : USINT;        // bit5 MD, bit4 ML weak, bit3 MH strong, bit0 no sensor
    xMagnetOk       : BOOL;         // MD and neither too weak nor too strong
    xSensorMissing  : BOOL;
    byEncI2cErr     : USINT;
    byEncAgc        : USINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
ecat_esp_out0 := byOut0;
ecat_esp_out1 := byOut1;
byIn0 := ecat_esp_in0;
byIn1 := ecat_esp_in1;

IF byIn0 <> byLastIn0 THEN
    udiCounterSeen := udiCounterSeen + 1;
    uiStaleCycles := 0;
ELSIF uiStaleCycles < 65535 THEN
    uiStaleCycles := uiStaleCycles + 1;
END_IF
byLastIn0 := byIn0;
xEspAlive := uiStaleCycles < 100;
xRoundTripOk := (byIn1 = byOut1);

uiEncRaw := USINT_TO_UINT(ecat_esp_in2) OR SHL(USINT_TO_UINT(ecat_esp_in3), 8);
rEncDeg := UINT_TO_REAL(uiEncRaw) * 360.0 / 4096.0;
diEncPos := UDINT_TO_DINT(USINT_TO_UDINT(ecat_esp_in5)
                       OR SHL(USINT_TO_UDINT(ecat_esp_in6), 8)
                       OR SHL(USINT_TO_UDINT(ecat_esp_in7), 16)
                       OR SHL(USINT_TO_UDINT(ecat_esp_in8), 24));
rEncTurns := DINT_TO_REAL(diEncPos) / 4096.0;
byEncStatus := ecat_esp_in4;
xSensorMissing := (byEncStatus AND 16#01) <> 0;
xMagnetOk := ((byEncStatus AND 16#20) <> 0) AND ((byEncStatus AND 16#18) = 0) AND NOT xSensorMissing;
byEncI2cErr := ecat_esp_in9;
byEncAgc := ecat_esp_in10;
