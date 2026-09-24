# -*- coding: ascii -*-
# Full install: download the project to the PLC and start it. The only
# way to deploy a change that online change cannot take.
#
# Run ONLY as:
#   python codesys_scripts/rpc.py install --on-site
# which runs this with plc=download (the daemon's login guard refuses it
# otherwise) and insists someone can power-cycle the PLC if it wedges.
#
# There is no "stop first" step any more. It used to attach with a first
# login and stop the app before downloading, but measurements on
# 2026-09-25 (plc_guard.py) showed every login applies the project/PLC
# difference, so that first login already was the transfer. A login with
# OnlineChangeOption.Never stops the application itself and downloads;
# no dialog came up in scripting on CODESYS 3.5.22.
#
# Note: a download takes the PLC TCP server down briefly. Any external
# client (UI / scripted harness) must reconnect afterwards.

import time

proj = projects.primary
app = proj.active_application
oapp = online.create_online_application(app)
print("project:", proj.path)

t0 = time.time()
oapp.login(OnlineChangeOption.Never, False)
print("download done in %.1fs, state: %s" % (time.time() - t0,
                                             oapp.application_state))

try:
    oapp.start()
except Exception as ex:
    if "is run" not in str(ex) and "already" not in str(ex).lower():
        raise
time.sleep(1.0)
print("post-start state:", oapp.application_state)
if "run" not in str(oapp.application_state).lower():
    raise RuntimeError("application did not start")

try:
    oapp.logout()
except Exception as ex:
    print("logout err (ignored):", ex)
