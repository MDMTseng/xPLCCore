# -*- coding: ascii -*-
# Cold-reset the application on the PLC, then start it again. Did NOT
# recover a wedged EtherCAT layer (2026-09-30: only a PLC restart did).
# Logs in with OnlineChangeOption.Try: run it only when the project matches
# what the PLC runs. Power the delta off first.
#
#   rpc.py exec --file jobs/templates/cold_reset.py

proj = projects.primary
app = proj.active_application
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)
try:
    print("cold reset...")
    try: oapp.reset(ResetOption.Cold)
    except Exception as ex: print("reset err:", ex)
    print("starting...")
    try: oapp.start()
    except Exception as ex: print("start err:", ex)
    print("done")
finally:
    try: oapp.logout()
    except Exception: pass
