# -*- coding: ascii -*-
# Pin down the correct online-read expression format.

proj = projects.primary
app = proj.active_application
oapp = online.create_online_application(app)

print("is_logged_in:        ", oapp.is_logged_in)
print("application_state:   ", oapp.application_state)
print("operation_state:     ", oapp.operation_state)

# Try several prefix forms against a known-simple symbol.
CANDIDATES = [
    "reelpullmotor.nAxisState",
    "reelpullmotor",
    "Application.reelpullmotor.nAxisState",
    "Application.reelpullmotor",
    "Device.Application.reelpullmotor.nAxisState",
    "AxisGroupSM.reelMoveRelative.Error",
    "Application.AxisGroupSM.reelMoveRelative.Error",
]

print("\n== read_value attempts ==")
for expr in CANDIDATES:
    try:
        v = oapp.read_value(expr)
        print("  OK  {!r:<70} = {!r}".format(expr, v))
    except Exception as ex:
        msg = str(ex)
        if len(msg) > 90:
            msg = msg[:90] + "..."
        print("  ERR {!r:<70} : {}".format(expr, msg))
