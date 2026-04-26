# -*- coding: ascii -*-
# B1 diagnostic: dump the DBG_SetCoord* + AxisGroupSMScans GVL fields.
# Run via the daemon during the SetCoord1 repro to read what the PLC
# latched. Outputs simple "key=value" lines that the host driver parses.

import time

proj = projects.primary
app = proj.active_application
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)
try:
    SYMBOLS = [
        "GVL.AxisGroupSMScans",
        "GVL.DBG_SetCoord1AcceptMs",
        "GVL.DBG_SetCoordExecuteRiseMs",
        "GVL.DBG_SetCoordBusyFirstMs",
        "GVL.DBG_SetCoordDoneMs",
        "GVL.DBG_SetCoordErrorMs",
        "GVL.DBG_SetCoordLastErrorID",
        "GVL.DBG_SetCoordExecuteFallMs",
        "AxisGroupSM.RuntimeMs",
        "AxisGroupSM.AxisGroupManagerFb._eState",
        "GVL.LastUiPingMs",
        "GVL.UiPingCount",
        "GVL.LastErrorSource",
        "GVL.LastErrorID",
        "GVL.CoordSystemConfigured",
        "GVL.bVirtualMotorsMode",
        "AxisGroupSM.SetCoordTransformFb.Busy",
        "AxisGroupSM.SetCoordTransformFb.Done",
        "AxisGroupSM.SetCoordTransformFb.Error",
        "AxisGroupSM.SetCoordTransformFb.ErrorID",
        "AxisGroupSM.ApplyCoordTransform",
    ]
    print("== DBG snapshot ==")
    print("wall_t=%.6f" % time.time())
    for sym in SYMBOLS:
        try:
            v = oapp.read_value(sym)
        except Exception as ex:
            v = "ERR:" + str(ex)[:40]
        print("%s=%s" % (sym, v))
    print("== end ==")
finally:
    try: oapp.logout()
    except Exception: pass
