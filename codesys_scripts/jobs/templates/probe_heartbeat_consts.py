proj = projects.primary
app = proj.active_application
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)
try:
    for s in [
        "AxisGroupSM.HEARTBEAT_INTERVAL_MS",
        "AxisGroupSM.PENDING_RETRY_MAX_SCANS",
        "AxisGroupSM.PendingMoveDoneRetryScans",
        "AxisGroupSM.PendingStChgRetryScans",
        "GVL.SelfReentryCount",
    ]:
        try:
            v = oapp.read_value(s)
            print("%s=%s" % (s, v))
        except Exception as ex:
            print("%s ERR %s" % (s, str(ex)[:80]))
finally:
    try: oapp.logout()
    except Exception: pass
