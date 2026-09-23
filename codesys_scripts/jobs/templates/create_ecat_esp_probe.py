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
# Firmware (firmware/easycat_esp32): in0 = live counter, in1 echoes out1,
# out0 bit 0 = ESP32 LED.
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
    byIn0           : USINT;        // ESP32 counter
    byIn1           : USINT;        // echo of out1
    byLastIn0       : USINT;
    udiCounterSeen  : UDINT;        // cycles the counter moved
    xRoundTripOk    : BOOL;
END_VAR
"""

IMPL = """ecat_esp_out0 := byOut0;
ecat_esp_out1 := byOut1;
byIn0 := ecat_esp_in0;
byIn1 := ecat_esp_in1;

IF byIn0 <> byLastIn0 THEN
    udiCounterSeen := udiCounterSeen + 1;
END_IF
byLastIn0 := byIn0;
xRoundTripOk := (byIn1 = byOut1);
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
