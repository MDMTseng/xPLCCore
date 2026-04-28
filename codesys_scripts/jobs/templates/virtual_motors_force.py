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
    # Cycle FALSE->TRUE so the derived flag re-opens the gate even if the
    # TTL TON (fbVirtualMotorsExpire) is latched from a previous session
    # (see memory: virtual_motors_gate_ton_reset.md). Under PLC load the
    # FALSE window must be long enough for the Comm task to observe IN's
    # falling edge AND for the PLC scan to clear TON.Q before we re-force.
    oapp.set_prepared_value("GVL.bVirtualMotorsMode_Request", "FALSE")
    oapp.force_prepared_values()
    # Poll until TON.Q has dropped (IN=FALSE -> Q=FALSE same scan, but online
    # forces propagate over multiple Comm cycles -- give it up to 2s).
    deadline = time.time() + 2.0
    q_dropped = False
    while time.time() < deadline:
        try:
            q = oapp.read_value("GVL.fbVirtualMotorsExpire.Q")
            if "FALSE" in str(q):
                q_dropped = True
                break
        except Exception:
            pass
        time.sleep(0.1)
    if not q_dropped:
        print("WARN: TON.Q did not drop within 2s after Request:=FALSE")

    oapp.set_prepared_value("GVL.bVirtualMotorsMode_Request", "TRUE")
    oapp.force_prepared_values()
    # Now wait for derived flag to come up.
    deadline = time.time() + 2.0
    derived_up = False
    while time.time() < deadline:
        try:
            d = oapp.read_value("GVL.bVirtualMotorsMode")
            if "TRUE" in str(d):
                derived_up = True
                break
        except Exception:
            pass
        time.sleep(0.1)

    req = oapp.read_value("GVL.bVirtualMotorsMode_Request")
    derived = oapp.read_value("GVL.bVirtualMotorsMode")
    print("bVirtualMotorsMode_Request =", req)
    print("bVirtualMotorsMode         =", derived)
    if not derived_up:
        print("WARN: derived flag is not TRUE -- gate closed, TTL may be latched.")
finally:
    oapp.logout()
