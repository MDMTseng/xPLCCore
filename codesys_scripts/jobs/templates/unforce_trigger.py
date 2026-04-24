# -*- coding: ascii -*-
proj = projects.primary
app_obj = None
for app in list(proj.find("Application", True) or []):
    app_obj = app; break
oapp = online.create_online_application(app_obj)
oapp.login(OnlineChangeOption.Try, False)
try:
    oapp.unforce_all_values()
    oapp.set_prepared_value("GVL.bRunMsgPackTests", "FALSE")
    oapp.force_prepared_values()
    import time; time.sleep(0.1)
    oapp.unforce_all_values()
    print("trigger now:", oapp.read_value("GVL.bRunMsgPackTests"))
finally:
    oapp.logout()
