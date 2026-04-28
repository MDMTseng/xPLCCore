# -*- coding: ascii -*-
# Find correct fully-qualified symbol path for GroupReadStatusFb fields and
# virtual-motors gate, then read them online to diagnose ErrorStop=TRUE,
# ErrorID=0 condition.

proj = projects.primary
app = proj.active_application
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)
if not oapp.is_logged_in:
    print("NOT LOGGED IN -- aborting"); raise SystemExit(1)

candidates = [
    "GVL.fbVirtualMotorsExpire.Q",
    "GVL.fbVirtualMotorsExpire.IN",
    "GVL.fbVirtualMotorsExpire.ET",
    "GVL.fbVirtualMotorsExpire.PT",
    "GVL.VIRTUAL_MOTORS_TTL",
    "AxisGroupSM.GroupReadStatusFb.GroupState",
    "AxisGroupSM.GroupReadStatusFb.ErrorID",
    "AxisGroupSM.GroupReadStatusFb.LastAcceptedMovementId",
    "Application.PLC_PRG.AxisGroupSM_0.GroupReadStatusFb.GroupErrorStop",
    "Application.PLC_PRG.AxisGroupSM_0.GroupReadStatusFb.ErrorID",
    "Application.PLC_PRG.AxisGroupSM_0.GroupReadStatusFb.GroupState",
    "Application.PLC_PRG.AxisGroupSM_0.GroupReadStatusFb.Error",
    "Application.AxisGroupSM.GroupReadStatusFb.GroupErrorStop",
    "AxisGroupSM.GroupReadStatusFb.GroupErrorStop",
    "Application.PLC_PRG.AxisGroupSM.GroupReadStatusFb.GroupErrorStop",
    "GVL.bVirtualMotorsMode",
    "GVL.bVirtualMotorsMode_Request",
    "GVL.AxisGroupManagerFb.SMC_GroupReset.Done",
    "GVL.AxisGroupManagerFb.SMC_GroupReset.Error",
    "GVL.AxisGroupManagerFb.SMC_GroupReset.ErrorID",
    "Application.GVL.AxisGroupManagerFb.SMC_GroupReset.Done",
    "Application.PLC_PRG.AxisGroupSM_0.GroupReadStatusFb.Status",
]

print("==== probing candidate symbols ====")
for sym in candidates:
    try:
        v = oapp.read_value(sym)
        print("  OK  {} = {}".format(sym, v))
    except Exception as e:
        msg = str(e).strip().splitlines()[0] if str(e) else type(e).__name__
        if "Invalid expression" not in msg:
            print("  ERR {} -> {}".format(sym, msg[:80]))

# Also search the symbol tree for AxisGroupSM_0 instance.
def walk(o, depth=0):
    try:
        n = o.get_name()
    except Exception:
        n = "?"
    yield depth, n, o
    try:
        for c in o.get_children():
            for k in walk(c, depth+1):
                yield k
    except Exception:
        pass

print("\n==== top-level POU instance hunt ====")
for top in proj.get_children():
    try:
        nm = top.get_name()
    except Exception:
        continue
    if nm in ("PLC_PRG", "Application"):
        for d, n, _ in walk(top):
            if d <= 3 and ("AxisGroupSM" in n or "AxisGroup" in n):
                print("  {}{}".format("  "*d, n))
