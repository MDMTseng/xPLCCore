# -*- coding: ascii -*-
import time
proj = projects.primary
app_obj = None
for app in list(proj.find("Application", True) or []):
    app_obj = app; break
oapp = online.create_online_application(app_obj)
oapp.login(OnlineChangeOption.Try, False)
try:
    print("before:", oapp.read_value("GVL.bRunMsgPackTests"))

    # Try force path
    oapp.set_prepared_value("GVL.bRunMsgPackTests", "TRUE")
    oapp.force_prepared_values()
    time.sleep(0.2)
    print("after force TRUE:", oapp.read_value("GVL.bRunMsgPackTests"))
    print("forced exprs:", list(oapp.get_forced_expressions() or []))

    # unforce to let PLC see the value normally, then set FALSE
    oapp.unforce_all_values()
    time.sleep(0.1)
    print("after unforce:", oapp.read_value("GVL.bRunMsgPackTests"))
finally:
    oapp.logout()
