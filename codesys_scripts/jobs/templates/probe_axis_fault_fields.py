# -*- coding: ascii -*-
# Probe per-axis fault fields. Memory note (3 days old) said bError works but
# nErrorID / LastError / etc. don't on this DS402 drive. Confirm and look for
# anything that returns a non-zero error code -- need it for W5.
proj = projects.primary
app = proj.active_application
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)
if not oapp.is_logged_in:
    print("NOT LOGGED IN after login() -- aborting"); raise SystemExit(1)

axes = [
    "IoConfig_Globals.EAxis0",
    "IoConfig_Globals.EAxis1",
    "IoConfig_Globals.EAxis2",
    "reelpullmotor",
]
candidate_fields = [
    "bError",
    "nAxisState",
    "nErrorID",
    "LastError",
    "nLastError",
    "nAxisError",
    "nDriveError",
    "wDriveError",
    "wState",
    "wCommunicationState",
    "wDS402StatusWord",
    "wStatusWord",
    "wErrorCode",
    "ErrorCode",
    "Diag",
    "diag.diHistoryLevel",
    "Status",
    "fbeFB",
]

for axis in axes:
    print("=== %s ===" % axis)
    for f in candidate_fields:
        expr = "%s.%s" % (axis, f)
        try:
            v = oapp.read_value(expr)
            print("  %-30s = %s" % (f, v))
        except Exception as e:
            msg = str(e).strip().splitlines()[0] if str(e) else type(e).__name__
            if "Invalid expression" not in msg:
                print("  %-30s ERR %s" % (f, msg))
