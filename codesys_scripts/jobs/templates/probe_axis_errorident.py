# -*- coding: ascii -*-
# Probe SM3 ErrorIdent fields per axis. Earlier probe_axis_fault_fields.py
# only checked nErrorID/LastError-style flat fields and reported nothing
# usable. ErrorIdent.SourceErrorID is the standard SM3 diagnostic-code
# location and the natural feed for the new GET_MACHINE_STATE 'axes_err_id'
# array. This script confirms the field exists and is online-readable on
# all four axes before the PLC change is deployed.
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
    "ErrorIdent.SourceErrorID",
    "ErrorIdent.Source",
    "ErrorIdent.ErrorID",
    "ErrorIdent.AxisErrorID",
    "ErrorIdent.iLine",
    "ErrorIdent.DiagExtIdent",
    "wDS402StatusWord",
    "uiDriveInterfaceError",
]

for axis in axes:
    print("=== %s ===" % axis)
    for f in candidate_fields:
        expr = "%s.%s" % (axis, f)
        try:
            v = oapp.read_value(expr)
            print("  %-32s = %s" % (f, v))
        except Exception as e:
            msg = str(e).strip().splitlines()[0] if str(e) else type(e).__name__
            if "Invalid expression" in msg or "Symbol" in msg or "not exist" in msg.lower():
                print("  %-32s NOT FOUND" % f)
            else:
                print("  %-32s ERR %s" % (f, msg))
