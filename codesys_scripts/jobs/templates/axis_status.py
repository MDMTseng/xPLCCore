# -*- coding: ascii -*-
# Dump the full status of reelpullmotor + the move FB + the AxisGroupManager
# state machine, so we can see WHY the axis is in errorstop.

proj = projects.primary
oapp = online.create_online_application(proj.active_application)
if not oapp.is_logged_in:
    print("NOT logged in to PLC")
    raise SystemExit(1)

def r(expr):
    try:
        v = oapp.read_value(expr)
    except Exception as ex:
        return "<err: {}>".format(ex)
    return v

AXIS_FIELDS = [
    "nAxisState",
    "nErrorID",
    "nErrorClass",
    "bRegulatorRealState",
    "bRegulatorOn",
    "bDriveInterfaceError",
    "wCommunicationState",
    "bCommunication",
    "fActPosition",
    "fSetPosition",
    "fActVelocity",
    "fSetVelocity",
    "fPositionPeriod",
]

MOVE_FIELDS = [
    "Execute",
    "Done",
    "Busy",
    "Active",
    "Error",
    "ErrorID",
    "CommandAborted",
    "Distance",
    "Velocity",
    "Acceleration",
    "Deceleration",
    "Jerk",
]

POWER_FIELDS = [
    "Enable",
    "bRegulatorOn",
    "bDriveStart",
    "Status",
    "bRegulatorRealState",
    "bDriveStartRealState",
    "Error",
    "ErrorID",
]

print("==== reelpullmotor ====")
for f in AXIS_FIELDS:
    print("  {:<30} = {}".format(f, r("reelpullmotor." + f)))

print("\n==== AxisGroupSM.reelMoveRelative ====")
for f in MOVE_FIELDS:
    print("  {:<30} = {}".format(f, r("AxisGroupSM.reelMoveRelative." + f)))

print("\n==== AxisGroupSM.fbAxisGroupManager ====")
for path in ("fbAxisGroupManager._eState",
             "fbAxisGroupManager._preState",
             "fbAxisGroupManager.fbPowerReel.Enable",
             "fbAxisGroupManager.fbPowerReel.bRegulatorOn",
             "fbAxisGroupManager.fbPowerReel.bDriveStart",
             "fbAxisGroupManager.fbPowerReel.Status",
             "fbAxisGroupManager.fbPowerReel.Error",
             "fbAxisGroupManager.fbPowerReel.ErrorID",
             "fbAxisGroupManager.fbResetReel.Execute",
             "fbAxisGroupManager.fbResetReel.Done",
             "fbAxisGroupManager.fbResetReel.Busy",
             "fbAxisGroupManager.fbResetReel.Error",
             "fbAxisGroupManager.fbResetReel.ErrorID",
             "fbAxisGroupManager.bReelPowerReq"):
    print("  {:<55} = {}".format(path, r("AxisGroupSM." + path)))
