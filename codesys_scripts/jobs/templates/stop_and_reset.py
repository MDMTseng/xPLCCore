# -*- coding: ascii -*-
# Stop the running application and reset it (warm by default), BEFORE the
# new code is imported: step 3 of tools/deploy.py (owner's sequence,
# 2026-10-05: no motion -> Error state -> reset -> download -> start).
#
#   rpc.py exec --plc keep --label stop_and_reset --file jobs/templates/stop_and_reset.py
#
# The login is Keep: plc_guard lets it through only while the project
# matches what the PLC runs, so this job can never transfer code. Set
# RESET = "cold" above this file's code (deploy.py prepends it) for a cold
# reset (also clears RETAIN).
import time

try:
    RESET
except NameError:
    RESET = "warm"

proj = projects.primary
app = proj.active_application
oapp = online.create_online_application(app)
t0 = time.time()
oapp.login(OnlineChangeOption.Keep, False)
print("logged in (%.1fs), state: %s" % (time.time() - t0, oapp.application_state))
try:
    t1 = time.time()
    oapp.stop()
    print("stopped (%.1fs), state: %s" % (time.time() - t1, oapp.application_state))
    t2 = time.time()
    oapp.reset(ResetOption.Cold if RESET == "cold" else ResetOption.Warm)
    print("reset %s done (%.1fs), state: %s" % (RESET, time.time() - t2, oapp.application_state))
finally:
    try:
        oapp.logout()
    except Exception as ex:
        print("logout err (ignored):", ex)
