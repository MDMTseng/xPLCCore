# -*- coding: ascii -*-
# Download the (stopped, reset) application, wait, start it, and watch the
# EtherCAT master come up in the same session: step 5 of tools/deploy.py.
#
#   rpc.py exec --plc download --label deploy_download --file jobs/templates/download_wait_start.py
#
# deploy.py prepends the parameters:
#   MAX_DOWNLOAD_S  a download slower than this is not started (2026-10-05:
#                   the two downloads after which EtherCAT never came up took
#                   83.5 s and 92 s; good ones 15-32 s)
#   SETTLE_S        wait after the download before start (an IDE user does
#                   not press start in the same second)
#   BUS_WAIT_S      how long to wait for xConfigFinished after start
import time

try:
    MAX_DOWNLOAD_S
except NameError:
    MAX_DOWNLOAD_S = 45.0
try:
    SETTLE_S
except NameError:
    SETTLE_S = 5.0
try:
    BUS_WAIT_S
except NameError:
    BUS_WAIT_S = 30.0

MASTER = "IoConfig_Globals.EtherCAT_Master_SoftMotion."

proj = projects.primary
app = proj.active_application
oapp = online.create_online_application(app)
t0 = time.time()
oapp.login(OnlineChangeOption.Never, False)
dl = time.time() - t0
print("download done in %.1fs, state: %s" % (dl, oapp.application_state))
try:
    if dl > MAX_DOWNLOAD_S:
        print("DOWNLOAD SLOW: %.1fs > %.0fs -- application NOT started" % (dl, MAX_DOWNLOAD_S))
    else:
        time.sleep(SETTLE_S)
        try:
            oapp.start()
        except Exception as ex:
            if "is run" not in str(ex) and "already" not in str(ex).lower():
                raise
        time.sleep(1.0)
        print("post-start state:", oapp.application_state)
        t1 = time.time()
        up = False
        last = None
        while time.time() - t1 < BUS_WAIT_S:
            try:
                last = str(oapp.read_value(MASTER + "xConfigFinished"))
            except Exception as ex:
                last = "read err: %s" % ex
            if "TRUE" in last.upper():
                up = True
                break
            time.sleep(1.0)
        try:
            msg = str(oapp.read_value(MASTER + "LastMessage"))
        except Exception as ex:
            msg = "read err: %s" % ex
        print("ethercat: %s after %.1fs (xConfigFinished %s, LastMessage %s)"
              % ("UP" if up else "NOT UP", time.time() - t1, last, msg))
finally:
    try:
        oapp.logout()
    except Exception as ex:
        print("logout err (ignored):", ex)
