# -*- coding: ascii -*-
# Clear the virtual-motors force. After this the derived GVL.bVirtualMotorsMode
# returns to FALSE on the next PLC cycle and EV_HOME_GO_FORCE_SKIP is ignored.
proj = projects.primary
app_obj = None
for app in list(proj.find("Application", True) or []):
    app_obj = app; break
oapp = online.create_online_application(app_obj)
oapp.login(OnlineChangeOption.Try, False)
try:
    oapp.unforce_all_values()
    import time; time.sleep(0.2)
    req = oapp.read_value("GVL.bVirtualMotorsMode_Request")
    derived = oapp.read_value("GVL.bVirtualMotorsMode")
    print("bVirtualMotorsMode_Request =", req)
    print("bVirtualMotorsMode         =", derived)
finally:
    oapp.logout()
