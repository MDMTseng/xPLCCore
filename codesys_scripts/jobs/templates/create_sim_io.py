# -*- coding: ascii -*-
# Create PRG_SimIo: what the simulated peripherals need to see of the PLC,
# counted every 1 ms in EtherCAT_Task.
#
#   rpc.py exec --file jobs/templates/create_sim_io.py
#
# The real cameras are hardware-triggered by PLC output pulses, and the
# renderer arms its result wait *before* the pulse, so a simulated vision
# system must answer after the pulse, not whenever it likes. The pulses
# are 1-5 ms wide -- too short for anything outside the PLC to sample --
# so this counts rising edges here and PRG_EcatEspHttp publishes the
# counters at GET /v for tools/sim/vision_mock.py to poll.
#
# Pins are CalibPage.tsx IO_Pins.O indices = bits of
# AxisGroupSM.DigitalOutputBits:
#   6 ReelAdv, 8 CAM_Side, 10 CAM_Btm, 12 CAM_FlexFeeder, 14 CAM_Top
# The arm's actual X/Y is latched at every bottom-camera edge: the bottom
# camera's nozzle calibration moves the arm by 1 mm steps and expects the
# image to shift accordingly.
#
# Read-only with respect to the machine: it only watches outputs.
# Idempotent. Builds; downloading is a separate step.

from System import Guid

NAME = "PRG_SimIo"
TASK = "EtherCAT_Task"
BUILD = Guid("{97f48d64-a2a3-4856-b640-75c046e37ea9}")

DECL = """PROGRAM PRG_SimIo
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
"""

IMPL = """uliRise := AxisGroupSM.DigitalOutputBits AND NOT uliPrev;
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
