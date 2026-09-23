# -*- coding: ascii -*-
# Set Modbus_COM's ComPort, then build.
#
#   rpc.py exec --file jobs/templates/set_mb_comport.py
#
# Edit COMPORT below first (the daemon's exec takes no arguments).
#
# CODESYS COM numbers belong to the PLC runtime, not to the dev PC. The
# project carried ComPort = 3, which matches the PC-side USB-485 adapter
# (PluginHello.tsx defaults to 'COM3') -- the machine's previous PLC had
# no RS-485 at all. On this SCIPC, COM3 with nothing attached still
# returned bytes (RESPONSE_CRC_FAIL on every request), so it is not the
# A/B/GND terminal. Scan with the feeder connected:
#
#   reads 1281 (0x0501)  -> right port: 19200 baud, address 1
#   RESPONSE_TIMEOUT     -> a real port, but not the one wired to the feeder
#   RESPONSE_CRC_FAIL    -> something else that talks back (as COM3 did)
#
# Downloading is a separate step.

from System import Guid

DEVICE = "Modbus_COM"
PARAM_ID = 9206  # ComPort
COMPORT = 3

proj = projects.primary
d = proj.find(DEVICE, True)[0]
done = False
for c in d.connectors:
    for prm in c.host_parameters:
        if int(prm.id) == PARAM_ID:
            print("ComPort %s -> %d" % (prm.value, COMPORT))
            prm.value = str(COMPORT)
            done = True
if not done:
    print("ABORT: ComPort parameter not found")
    raise SystemExit(1)

B = Guid("{97f48d64-a2a3-4856-b640-75c046e37ea9}")
system.clear_messages(B)
proj.active_application.generate_code()
errs = [m for m in system.get_message_objects(B) if "error" in str(m.severity).lower()]
for m in errs:
    print("[ERR] %s" % m.text)
print("build errors: %d" % len(errs))
