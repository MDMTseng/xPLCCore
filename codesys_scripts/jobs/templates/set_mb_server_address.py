# -*- coding: ascii -*-
# Set SmartBowlFeeder's Modbus ServerAddress, then build.
#
#   rpc.py exec --file jobs/templates/set_mb_server_address.py
#
# Edit ADDRESS below first (the daemon's exec takes no arguments).
#
# Diagnostic use: point the master at an address nobody answers to.
#   still RESPONSE_CRC_FAIL -> the master is reading its own transmission
#                              back (RS-485 echo), not a garbled reply
#   RESPONSE_TIMEOUT        -> the earlier bytes really came from the
#                              feeder: look at baud / line settings
# Restore with ADDRESS = 1. Downloading is a separate step.

from System import Guid

DEVICE = "SmartBowlFeeder"
PARAM_ID = 9100  # ServerAddress
ADDRESS = 1      # the feeder's real address (register 0x0039, low byte)

addr = ADDRESS

proj = projects.primary
d = proj.find(DEVICE, True)[0]
done = False
for c in d.connectors:
    for prm in c.host_parameters:
        if int(prm.id) == PARAM_ID:
            print("ServerAddress %s -> %d" % (prm.value, addr))
            prm.value = str(addr)
            print("now %s" % prm.value)
            done = True
if not done:
    print("ABORT: ServerAddress parameter not found")
    raise SystemExit(1)

B = Guid("{97f48d64-a2a3-4856-b640-75c046e37ea9}")
system.clear_messages(B)
proj.active_application.generate_code()
errs = [m for m in system.get_message_objects(B) if "error" in str(m.severity).lower()]
for m in errs:
    print("[ERR] %s" % m.text)
print("build errors: %d" % len(errs))
