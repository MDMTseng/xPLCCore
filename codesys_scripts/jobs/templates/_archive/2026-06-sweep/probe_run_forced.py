# -*- coding: ascii -*-
import time
proj = projects.primary
app_obj = None
for app in list(proj.find("Application", True) or []):
    app_obj = app; break
oapp = online.create_online_application(app_obj)
oapp.login(OnlineChangeOption.Try, False)
try:
    print("app_state:", oapp.application_state)
    print("op_state :", oapp.operation_state)
    oapp.unforce_all_values()
    time.sleep(0.05)
    print("trigger before:", oapp.read_value("GVL.bRunMsgPackTests"))
    print("bDone before  :", oapp.read_value("GVL.fbMsgPackTests.bDone"))

    # Force FALSE first (guarantees clean edge)
    oapp.set_prepared_value("GVL.bRunMsgPackTests", "FALSE")
    oapp.force_prepared_values()
    time.sleep(0.1)
    print("after force FALSE:", oapp.read_value("GVL.bRunMsgPackTests"))

    # Force TRUE -- edge fires
    oapp.set_prepared_value("GVL.bRunMsgPackTests", "TRUE")
    oapp.force_prepared_values()
    time.sleep(0.5)
    print("after force TRUE :", oapp.read_value("GVL.bRunMsgPackTests"))
    print("bDone  :", oapp.read_value("GVL.fbMsgPackTests.bDone"))
    print("passed :", oapp.read_value("GVL.fbMsgPackTests.uiPassed"))
    print("failed :", oapp.read_value("GVL.fbMsgPackTests.uiFailed"))
    print("first  :", oapp.read_value("GVL.fbMsgPackTests.sFirstFail"))
    print("summary:", oapp.read_value("GVL.fbMsgPackTests.sSummary"))

    oapp.unforce_all_values()
finally:
    oapp.logout()
