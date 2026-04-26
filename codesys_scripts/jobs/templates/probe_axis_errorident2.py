# -*- coding: ascii -*-
# Wider sweep for the actual SM_Drive_GenericDSP402 error-code source on
# this drive type. ErrorIdent.* didn't resolve (SM3 generic field is
# absent on DS402 axes). uiDriveInterfaceError exists but returns 0;
# need to find a field that holds a real diagnostic code.
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
# Mix of SM3-base, DS402-base, and SoftMotion-Lite candidates.
candidate_fields = [
    # Already known to work / not work
    "bError",
    "uiDriveInterfaceError",
    # DS402 status / error word candidates
    "wErrorCode",
    "ErrorCode",
    "ErrorID",
    "wDriveError",
    "iDriveError",
    "DriveError",
    "DriveStatus",
    "Diagnosis",
    "Diag",
    "nDriveInterfaceError",
    "uiCommunicationState",
    "wCommunicationState",
    # SM3 generic with different paths
    "udiAxisError",
    "Status",
    "wState",
    "nState",
    "LastError",
    "nLastError",
    "ErrorIdentLast",
    "Errorident.SourceErrorID",
    "errorIdent.SourceErrorID",
    # Common DS402-specific SDO mirror fields
    "wControlWord",
    "wStatusWord",
    "wModeOfOperation",
    "wActModeOfOperation",
    # IoDrvEtherCAT / SoftMotion mappings
    "FB.bError",
    "FB.iErrorID",
    "FB.wErrorCode",
    "fb.wErrorCode",
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
            if "Invalid expression" in msg or "Symbol" in msg or "not exist" in msg.lower() or "could not" in msg.lower():
                pass  # silent: too many candidates to print every NOT FOUND
            else:
                print("  %-32s ERR %s" % (f, msg))
