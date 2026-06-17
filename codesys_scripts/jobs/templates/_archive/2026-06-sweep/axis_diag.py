# -*- coding: ascii -*-
# 1) Print the declaration section of AxisGroupSM to find the AxisGroupManager
#    instance name.
# 2) Try many known DS402/SoftMotion error-field names on reelpullmotor.

proj = projects.primary
oapp = online.create_online_application(proj.active_application)

# --- 1) Find the AxisGroupSM POU and print its declaration head ---
found = None
def walk(o):
    global found
    try:
        if o.get_name() == "AxisGroupSM":
            found = o
            return
    except Exception:
        pass
    try:
        for c in o.get_children():
            walk(c)
            if found: return
    except Exception:
        pass

for top in proj.get_children():
    walk(top)
    if found: break

if found is not None:
    print("==== AxisGroupSM declaration (first 60 lines) ====")
    try:
        txt = found.textual_declaration.text
    except Exception as ex:
        print("couldn't read declaration:", ex)
        txt = ""
    for i, line in enumerate(txt.splitlines()[:60], 1):
        print("  {:3d}: {}".format(i, line))
else:
    print("!! AxisGroupSM not found")

# --- 2) Probe error-related field names on reelpullmotor ---
print("\n==== reelpullmotor error-field probe ====")
CANDIDATES = [
    "nErrorID",                # SoftMotion standard, sometimes exists
    "LastError",
    "nLastError",
    "nAxisError",
    "nDriveError",
    "wDriveError",
    "ulDriveError",
    "bAxisErrorNeedAck",
    "bError",
    "bFault",
    "bDriveInterfaceError",    # already tried, keep for completeness
    "LastWarning",
    "nWarningCode",
    "wActErrorID",
    "wDiagnosisCode",
    "dwDiagnosisCode",
    "wState",
    "Status",
    "Diag",
    "diagnosis",
    "Details",
    "driveDiagnosis",
]
for f in CANDIDATES:
    try:
        v = oapp.read_value("reelpullmotor." + f)
        print("  OK  {:<30} = {}".format(f, v))
    except Exception as ex:
        msg = str(ex)[:70]
        print("  --  {:<30} : {}".format(f, msg))

# --- 3) Also probe whether there's a drive-subobject to inspect ---
print("\n==== drive-subobject probe ====")
for sub in ("DriveData", "driveData", "drv", "drive", "Drive",
            "axisRef", "m_AxisRef"):
    try:
        v = oapp.read_value("reelpullmotor." + sub)
        print("  OK  reelpullmotor.{:<20} = {!r}".format(sub, v))
    except Exception as ex:
        msg = str(ex)[:70]
        print("  --  reelpullmotor.{:<20} : {}".format(sub, msg))
