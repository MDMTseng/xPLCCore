# -*- coding: ascii -*-
# Read live variable values from the already-online application.
# Requires: CODESYS is logged into the PLC (green "online" state).

import traceback

proj = projects.primary
app = proj.active_application

print("== create online app ==")
try:
    oapp = online.create_online_application(app)
except Exception as ex:
    print("create_online_application failed:", ex)
    raise SystemExit(1)

print("online app created. dir:")
attrs = sorted(a for a in dir(oapp) if not a.startswith("_"))
for i in range(0, len(attrs), 6):
    print("  " + ", ".join(attrs[i:i+6]))

# Try common read method names
SYMBOLS = [
    "Application.AxisGroupSM.reelMoveRelative.Error",
    "Application.AxisGroupSM.reelMoveRelative.ErrorID",
    "Application.AxisGroupSM.reelMoveRelative.Done",
    "Application.AxisGroupSM.reelMoveRelative.Busy",
    "Application.AxisGroupSM.reelMoveRelative.Active",
    "Application.AxisGroupSM.reelMoveRelative.CommandAborted",
    "Application.AxisGroupSM.fbAxisGroupManager._eState",
    "Application.reelpullmotor.bRegulatorRealState",
    "Application.reelpullmotor.nAxisState",
    "Application.reelpullmotor.wCommunicationState",
    "Application.reelpullmotor.nCommunicationState",
    "Application.reelpullmotor.fActPosition",
    "Application.reelpullmotor.fSetPosition",
    "Application.reelpullmotor.fActVelocity",
    "Application.reelpullmotor.fSetVelocity",
    "Application.reelpullmotor.bDriveInterfaceError",
    "Application.reelpullmotor.nErrorID",
]

print("\n== trying read via online app ==")
# Many CODESYS scripting versions use read_values / read_list_of_values.
for method in ("read_values", "read_list_of_values", "read"):
    fn = getattr(oapp, method, None)
    if fn is None:
        print("  {:<22} -> not found".format(method))
        continue
    try:
        result = fn(SYMBOLS)
        print("  {:<22} -> OK".format(method))
        print("result type:", type(result))
        # Try to dump
        try:
            for k, v in zip(SYMBOLS, result):
                print("  {} = {!r}".format(k, v))
        except Exception:
            # result might be a dict
            try:
                for k, v in result.items():
                    print("  {} = {!r}".format(k, v))
            except Exception:
                print("  raw:", result)
        break
    except Exception as ex:
        print("  {:<22} -> err: {}".format(method, ex))

print("\n[done]")
