PROGRAM PRG_EcatStepper
VAR CONSTANT
    CYCLE_S             : LREAL := 0.001;   // EtherCAT_Task cycle, s (DT is a keyword)
    MAX_STEPS_PER_CYCLE : LREAL := 45.0;    // firmware faults above 50
END_VAR
VAR
    // --- interface ---
    xEnable         : BOOL;
    rTargetDeg      : LREAL;
    rMaxVelDeg      : LREAL := 180.0;       // deg/s
    rAccDeg         : LREAL := 1800.0;      // deg/s^2
    udiStepsPerRev  : UDINT := 6400;        // 200 full steps x 32 microsteps
    xSetZero        : BOOL;
    xReset          : BOOL;

    rActualDeg      : LREAL;
    rCmdDeg         : LREAL;
    xInPosition     : BOOL;
    xEnabled        : BOOL;
    xFault          : BOOL;
    byFault         : USINT;                // 1 comm lost, 2 setpoint jump

    // --- internals ---
    diActual        : DINT;                 // steps emitted, from the ESP32
    diCmd           : DINT;                 // setpoint sent this cycle
    diZero          : DINT;
    lrPos           : LREAL;                // profile position, steps
    lrVel           : LREAL;                // profile velocity, steps/s
    lrTarget        : LREAL;
    lrErr           : LREAL;
    lrVmax          : LREAL;
    lrAcc           : LREAL;
    lrVdes          : LREAL;
    lrStep          : LREAL;
    lrScale         : LREAL;                // steps per degree
    xActive         : BOOL;
    xLastSetZero    : BOOL;
    udiCmd          : UDINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
diActual := UDINT_TO_DINT(USINT_TO_UDINT(ecat_esp_in16)
                       OR SHL(USINT_TO_UDINT(ecat_esp_in17), 8)
                       OR SHL(USINT_TO_UDINT(ecat_esp_in18), 16)
                       OR SHL(USINT_TO_UDINT(ecat_esp_in19), 24));
xEnabled := (ecat_esp_in20 AND 16#01) <> 0;
xFault := (ecat_esp_in20 AND 16#02) <> 0;
byFault := ecat_esp_in21;

lrScale := UDINT_TO_LREAL(MAX(udiStepsPerRev, 1)) / 360.0;

// Zero at the current position; hold still there.
IF xSetZero AND NOT xLastSetZero THEN
    diZero := diActual;
    rTargetDeg := 0.0;
    lrPos := DINT_TO_LREAL(diActual);
    lrVel := 0.0;
END_IF
xLastSetZero := xSetZero;

rActualDeg := DINT_TO_LREAL(diActual - diZero) / lrScale;
lrTarget := DINT_TO_LREAL(diZero) + rTargetDeg * lrScale;

IF xEnable AND NOT xFault THEN
    IF NOT xActive THEN
        // Start from where the motor is, so the first setpoint is no jump.
        lrPos := DINT_TO_LREAL(diActual);
        lrVel := 0.0;
        xActive := TRUE;
    END_IF

    lrVmax := MIN(ABS(rMaxVelDeg) * lrScale, MAX_STEPS_PER_CYCLE / CYCLE_S);
    lrAcc := MAX(ABS(rAccDeg) * lrScale, 1.0);
    lrErr := lrTarget - lrPos;

    // Velocity that still stops exactly at the target: sqrt(2 a |e|),
    // capped at vmax; approach it at most a*dt per cycle.
    lrVdes := MIN(lrVmax, SQRT(2.0 * lrAcc * ABS(lrErr)));
    IF lrErr < 0.0 THEN
        lrVdes := -lrVdes;
    END_IF
    IF lrVdes > lrVel + lrAcc * CYCLE_S THEN
        lrVel := lrVel + lrAcc * CYCLE_S;
    ELSIF lrVdes < lrVel - lrAcc * CYCLE_S THEN
        lrVel := lrVel - lrAcc * CYCLE_S;
    ELSE
        lrVel := lrVdes;
    END_IF

    lrStep := lrVel * CYCLE_S;
    IF (lrErr >= 0.0 AND lrStep > lrErr) OR (lrErr <= 0.0 AND lrStep < lrErr) THEN
        lrStep := lrErr;                    // land on the target, no overshoot
        lrVel := 0.0;
    END_IF
    lrPos := lrPos + lrStep;
    diCmd := LREAL_TO_DINT(lrPos);
    ecat_esp_out2 := 16#01;                 // enable
ELSE
    // Disabled or faulted: follow the motor, send no motion.
    xActive := FALSE;
    lrVel := 0.0;
    lrPos := DINT_TO_LREAL(diActual);
    diCmd := diActual;
    IF xReset THEN
        ecat_esp_out2 := 16#02;             // fault reset, enable off
    ELSE
        ecat_esp_out2 := 16#00;
    END_IF
END_IF

rCmdDeg := (lrPos - DINT_TO_LREAL(diZero)) / lrScale;
xInPosition := xEnabled AND lrVel = 0.0 AND ABS(lrTarget - DINT_TO_LREAL(diActual)) < 1.0;

udiCmd := DINT_TO_UDINT(diCmd);
ecat_esp_out4 := UDINT_TO_USINT(udiCmd AND 16#FF);
ecat_esp_out5 := UDINT_TO_USINT(SHR(udiCmd, 8) AND 16#FF);
ecat_esp_out6 := UDINT_TO_USINT(SHR(udiCmd, 16) AND 16#FF);
ecat_esp_out7 := UDINT_TO_USINT(SHR(udiCmd, 24) AND 16#FF);
