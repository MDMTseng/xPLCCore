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
