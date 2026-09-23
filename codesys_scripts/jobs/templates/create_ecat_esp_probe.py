# -*- coding: ascii -*-
# Create PRG_EcatEsp: exercises the EasyCAT PRO + ESP32 slave from
# EtherCAT_Task.
#
#   rpc.py exec --file jobs/templates/create_ecat_esp_probe.py
#
# Why a program at all: CODESYS copies a mapped I/O variable to/from the
# process image in the task that USES it. ecat_esp_* are referenced by no
# program, and "Always update variables" would run them in the bus cycle
# task -- which on this project is a dangling reference (see
# doc/3-subsystems/ethercat_config.md). The slave reached OP, frames
# flowed, and every variable stayed 0 in both directions. Referencing
# them here puts them in EtherCAT_Task, next to the rest of the bus I/O.
#
# Firmware (firmware/easycat_esp32): in0 heartbeat, in1 echoes out1,
# in2..in10 the AS5600 encoder; out0 bit 0 = ESP32 LED.
#
#   xRoundTripOk   out1 came back on in1
#   udiCounterSeen number of cycles the counter moved
#
# Idempotent. Builds; downloading is a separate step.

from System import Guid

NAME = "PRG_EcatEsp"
TASK = "EtherCAT_Task"
BUILD = Guid("{97f48d64-a2a3-4856-b640-75c046e37ea9}")

DECL = """PROGRAM PRG_EcatEsp
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
"""

IMPL = """ecat_esp_out0 := byOut0;
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
