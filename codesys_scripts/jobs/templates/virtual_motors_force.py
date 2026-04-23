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
    import time
    # Always cycle FALSE->TRUE so the derived flag re-opens the gate even if
    # the TTL TON (fbVirtualMotorsExpire) is latched from a previous session
    # (see memory: virtual_motors_gate_ton_reset.md).
    oapp.set_prepared_value("GVL.bVirtualMotorsMode_Request", "FALSE")
    oapp.force_prepared_values()
    time.sleep(0.3)
    oapp.set_prepared_value("GVL.bVirtualMotorsMode_Request", "TRUE")
    oapp.force_prepared_values()
    time.sleep(0.3)
    req = oapp.read_value("GVL.bVirtualMotorsMode_Request")
    derived = oapp.read_value("GVL.bVirtualMotorsMode")
    print("bVirtualMotorsMode_Request =", req)
    print("bVirtualMotorsMode         =", derived)
    if "TRUE" not in str(derived):
        print("WARN: derived flag is not TRUE -- gate closed, TTL may be latched.")
finally:
    oapp.logout()
