# -*- coding: ascii -*-
# Snapshot of reMP_info reply-ring state. Used to investigate the M4 reply-path
# crash in FB_MpPacker.PackMapHeader (NULL pStart). Read once when healthy;
# the goal is to confirm whether buf pointers are valid before M4 fires.

proj = projects.primary
app = proj.active_application
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)
try:
    BASE = [
        "AxisGroupSM.RuntimeMs",
        "GVL.reMP_info_ridx.head",
        "GVL.reMP_info_ridx.tail",
        "GVL.reMP_info_buff_SIZE",
        "GVL.LastErrorSource",
        "GVL.LastErrorID",
        "GVL.ReMpDropCount",
        "AxisGroupSM.GroupReadStatusFb.LastAcceptedMovementId",
        "AxisGroupSM.FlyEventAvailableCount",
    ]
    for sym in BASE:
        try:
            v = oapp.read_value(sym)
        except Exception as ex:
            v = "ERR:" + str(ex)[:60]
        print("%s=%s" % (sym, v))

    # Walk every reply-ring slot. If buf is NULL => InitRuntime never ran or
    # something nulled it; if buf is the SAME for every slot => slots aliased
    # (would corrupt under concurrent fill).
    for i in range(8):
        for field in ("buf", "buf_capacity", "buf_size", "ele_count",
                      "whead", "useLargeElementCountField"):
            sym = "GVL.reMP_info_arr[%d].%s" % (i, field)
            try:
                v = oapp.read_value(sym)
            except Exception as ex:
                v = "ERR:" + str(ex)[:60]
            print("%s=%s" % (sym, v))
finally:
    try: oapp.logout()
    except Exception: pass
