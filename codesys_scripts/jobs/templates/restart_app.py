# -*- coding: ascii -*-
# Stop and start the application on the PLC (no download). Logs in with
# OnlineChangeOption.Try: run it only when the project matches what the PLC
# runs. Power the delta off first.
#
#   rpc.py exec --file jobs/templates/restart_app.py

proj = projects.primary
app = proj.active_application
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)
try:
    print("stopping...")
    try: oapp.stop()
    except Exception as ex: print("stop err:", ex)
    print("starting...")
    try: oapp.start()
    except Exception as ex: print("start err:", ex)
    print("done")
finally:
    try: oapp.logout()
    except Exception: pass
