# -*- coding: ascii -*-
# Force GVL.bVirtualMotorsMode_Request := TRUE so EV_HOME_GO_FORCE_SKIP is
# allowed to fire. The PLC-side TTL (GVL.VIRTUAL_MOTORS_TTL, default 10min)
# auto-expires the derived flag even if this force is left on, so a forgotten
# override cannot silently persist. Use virtual_motors_unforce.py to clear.
proj = projects.primary
app_obj = None
for app in list(proj.find("Application", True) or []):
    app_obj = app; break
oapp = online.create_online_application(app_obj)
oapp.login(OnlineChangeOption.Try, False)
try:
    oapp.set_prepared_value("GVL.bVirtualMotorsMode_Request", "TRUE")
    oapp.force_prepared_values()
    import time; time.sleep(0.2)
    req = oapp.read_value("GVL.bVirtualMotorsMode_Request")
    derived = oapp.read_value("GVL.bVirtualMotorsMode")
    print("bVirtualMotorsMode_Request =", req)
    print("bVirtualMotorsMode         =", derived)
finally:
    oapp.logout()
