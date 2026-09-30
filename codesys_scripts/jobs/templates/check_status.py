# -*- coding: ascii -*-
# Log in to the application and print a few TCP server / FSM status
# variables. Logs in with OnlineChangeOption.Try: run it only when the
# project matches what the PLC runs (plc_guard), or the login may online-
# change the PLC.
#
#   rpc.py exec --file jobs/templates/check_status.py

proj = projects.primary
app = proj.active_application
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)
try:
    for s in [
        "TCP_MSGPAK_Server.fbMyServer.xServerActive",
        "TCP_MSGPAK_Server.fbMyServer.xEnableConnection",
        "TCP_MSGPAK_Server.fbMyServer.xResetConnection",
        "TCP_MSGPAK_Server.fbMyServer.fbServer.xEnable",
        "TCP_MSGPAK_Server.fbMyServer.fbServer.xActive",
        "TCP_MSGPAK_Server.fbMyServer.fbServer.xError",
        "TCP_MSGPAK_Server.fbMyServer.fbServer.eError",
        "TCP_MSGPAK_Server.bEnableServer",
        "GVL.AxisGroupSMScans",
        "GVL.TxStallResetCount",
        "GVL.IdleResetCount",
        "GVL.SendStallDropCount",
        "TCP_MSGPAK_Server.fbMyServer.CLIENT.xActive",
        "TCP_MSGPAK_Server.fbMyServer.CLIENT.xError",
        "TCP_MSGPAK_Server.tx_stall_force_reset",
        "TCP_MSGPAK_Server.consecutive_stall_drops",
        "AxisGroupSM.AxisGroupManagerFb._eState",
    ]:
        try:
            v = oapp.read_value(s)
        except Exception as ex:
            v = "ERR:" + str(ex)[:80]
        print("%s=%s" % (s, v))
finally:
    try: oapp.logout()
    except Exception: pass
