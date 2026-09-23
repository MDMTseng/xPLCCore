# -*- coding: ascii -*-
# Create PRG_MbProbe: on-demand Modbus RTU diagnostics for the
# SmartBowlFeeder, called from the Comm task (the Modbus bus cycle task).
#
#   rpc.py exec --file jobs/templates/create_mb_probe.py
#
# Two things, both through the Modbus master's own COM port (a second
# handle on COM3 would fight the master for it):
#
#   Continuously copies the port diagnostics
#     uiSlaves / xAllOk  <- Modbus_Client_COM_Port (IoDrvModbusComPort)
#
#   On xRun := TRUE, executes configured channel iChannel once
#   (IoDrvModbus.ModbusChannel) and latches the result:
#     xError with eErr = UNDEFINED -> that channel index does not exist
#     xError with eErr = timeout   -> the frame went out, nobody answered:
#                                     wiring, A/B, termination, address, baud
#     xDone                        -> the feeder answered
#
# Nothing is written to the feeder unless the channel itself is a write.
# ModbusRequest2 (ad hoc function codes) would need the raw COM handle,
# which IoDrvModbusComPort does not expose.
#
# Idempotent: replaces the POU text and adds the task call only once.
# Builds and prints errors; downloading is a separate step.

from System import Guid

NAME = "PRG_MbProbe"
TASK = "Comm"
BUILD = Guid("{97f48d64-a2a3-4856-b640-75c046e37ea9}")

DECL = """PROGRAM PRG_MbProbe
VAR
    xRun        : BOOL;             // write TRUE to execute iChannel once
    iChannel    : INT := 0;         // configured channel index
    fbCh        : IoDrvModbus.ModbusChannel;
    xDone       : BOOL;             // latched result of the last run
    xError      : BOOL;
    eErr        : IoDrvModbus.MB_ErrorCodes;
    uiSlaves    : UINT;             // port: communicating slaves
    xAllOk      : BOOL;             // port: all slaves ok
    udiRuns     : UDINT;
    udiDone     : UDINT;
    udiErrors   : UDINT;
END_VAR
"""

IMPL = """fbCh(slave := SmartBowlFeeder, xExecute := xRun, iChannelIndex := iChannel);

uiSlaves := Modbus_Client_COM_Port.uiNumberOfCommunicatingSlaves;
xAllOk := Modbus_Client_COM_Port.xAllSlavesOk;

IF xRun AND (fbCh.xDone OR fbCh.xError) THEN
    xDone := fbCh.xDone;
    xError := fbCh.xError;
    eErr := fbCh.ModbusError;
    udiRuns := udiRuns + 1;
    IF fbCh.xDone THEN udiDone := udiDone + 1; END_IF
    IF fbCh.xError THEN udiErrors := udiErrors + 1; END_IF
    xRun := FALSE;
END_IF
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
