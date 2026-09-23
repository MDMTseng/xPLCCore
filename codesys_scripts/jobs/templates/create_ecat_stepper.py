# -*- coding: ascii -*-
# Create PRG_EcatStepper: the PLC side of the ESP32 open-loop CSP stepper
# (firmware/easycat_esp32/src/stepper.h), in EtherCAT_Task (1 ms).
#
#   rpc.py exec --file jobs/templates/create_ecat_stepper.py
#
# The simplest interface: write an angle, it goes there.
#
#   xEnable        drive enabled (starts from wherever the motor is)
#   rTargetDeg     absolute target, degrees from the zero point
#   rMaxVelDeg     deg/s        rAccDeg   deg/s^2
#   xSetZero       rising edge: current position becomes 0 deg
#   xReset         clear a latched fault (works with xEnable FALSE)
#   -> rActualDeg, xInPosition, xEnabled, xFault, byFault (1 comm, 2 jump)
#
# Cyclic synchronous position: every 1 ms this computes the next setpoint
# with a velocity-limited, acceleration-limited profile and sends it as an
# absolute step count; the ESP32 interpolates it into step pulses across
# the next cycle. Open loop: "actual" is steps emitted, not measured.
#
# Resolution: udiStepsPerRev must match the driver. 0.1 deg needs at least
# 3600 steps/rev, i.e. 200-step motor at 32 microsteps = 6400.
#
# Velocity is capped at 45 steps per cycle, under the firmware's 50: with
# 6400 steps/rev that is 45 000 steps/s = 2531 deg/s = 7 rev/s.
#
# Idempotent. Builds; downloading is a separate step.

from System import Guid

NAME = "PRG_EcatStepper"
TASK = "EtherCAT_Task"
BUILD = Guid("{97f48d64-a2a3-4856-b640-75c046e37ea9}")

DECL = """PROGRAM PRG_EcatStepper
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
"""

IMPL = """diActual := UDINT_TO_DINT(USINT_TO_UDINT(ecat_esp_in16)
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
"""


def t(v):
    try:
        if isinstance(v, unicode):
            return v.encode("utf-8", "replace")
        return str(v)
    except Exception:
        return "?"


proj = projects.primary
app = proj.active_application

pou = None
for o in app.get_children():
    if t(o.get_name()) == NAME:
        pou = o
if pou is None:
    pou = app.create_pou(NAME, PouType.Program)
    print("created %s" % NAME)
pou.textual_declaration.replace(DECL)
pou.textual_implementation.replace(IMPL)

task = None
for tc in proj.find("Task Configuration", True) or []:
    for tk in tc.get_children():
        if t(tk.get_name()) == TASK:
            task = tk
calls = [t(getattr(c, "name", c)) for c in task.pous]
if NAME not in calls:
    task.pous.add(NAME)
    print("added %s to %s (was %s)" % (NAME, TASK, calls))

system.clear_messages(BUILD)
app.generate_code()
errs = 0
for m in system.get_message_objects(BUILD):
    if "error" in str(getattr(m, "severity", "")).lower():
        errs += 1
        print("  [ERR] %s" % t(getattr(m, "text", m)))
print("build errors: %d" % errs)
