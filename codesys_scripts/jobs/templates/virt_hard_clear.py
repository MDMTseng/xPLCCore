# -*- coding: ascii -*-
# Hard-clear the virtual-motors force: overwrite to FALSE first (defeats any
# latched force value), then unforce_all_values, then read-back to confirm.
proj = projects.primary
app_obj = None
for app in list(proj.find("Application", True) or []):
    app_obj = app; break
oapp = online.create_online_application(app_obj)
oapp.login(OnlineChangeOption.Try, False)
try:
    # Step 1: replace force value with FALSE and commit.
    oapp.set_prepared_value("GVL.bVirtualMotorsMode_Request", "FALSE")
    oapp.force_prepared_values()
    import time; time.sleep(0.2)
    print("after force=FALSE:",
          oapp.read_value("GVL.bVirtualMotorsMode_Request"),
          oapp.read_value("GVL.bVirtualMotorsMode"))
    # Step 2: unforce everything.
    oapp.unforce_all_values()
    time.sleep(0.2)
    print("after unforce  :",
          oapp.read_value("GVL.bVirtualMotorsMode_Request"),
          oapp.read_value("GVL.bVirtualMotorsMode"))
finally:
    oapp.logout()
