# -*- coding: ascii -*-
# Drive the PLC-side MsgPack self-test harness (FB_MsgPackTests) in
# online mode and print results. Bypasses the web UI entirely.
#
# Prereqs (one-time, manual in CODESYS IDE):
#   1. Create POU skeleton FB_MsgPackTests in Application/COMM_FBs/
#      (Function Block). Right-click COMM_FBs -> Add Object ->
#      Function Block. Name it FB_MsgPackTests.
#   2. Right-click FB_MsgPackTests -> Add Object -> Method.
#      Create methods: RunAll (PRIVATE, no return) and Fail (PRIVATE,
#      no return; VAR_INPUT sMsg : STRING(250);).
#   3. Run import_all.py once -- it pushes the .st files on disk into
#      those skeletons. Confirm 0 build errors.
#   4. Download to PLC (IDE: Online -> Login with download, then Start).
#
# Then run this template: it toggles GVL.bRunMsgPackTests and reads
# results from GVL.fbMsgPackTests.*.

import time

proj = projects.primary
print("project:", proj.path)

# Locate Application for online ops
app_obj = None
for app in list(proj.find("Application", True) or []):
    app_obj = app; break
if app_obj is None:
    print("ERROR: no Application object found"); raise SystemExit(1)

oapp = online.create_online_application(app_obj)
try:
    oapp.login(OnlineChangeOption.Try, False)
except Exception as ex:
    print("login failed:", ex)
    print("  Is the PLC running and matching the project? "
          "If code changed, download via IDE first (Login with download).")
    raise

def write_bool(expr, val):
    oapp.set_prepared_value(expr, "TRUE" if val else "FALSE")
    oapp.write_prepared_values()

try:
    # Reset then trigger. R_TRIG inside FB fires on FALSE->TRUE edge.
    write_bool("GVL.bRunMsgPackTests", False)
    time.sleep(0.05)
    write_bool("GVL.bRunMsgPackTests", True)

    # RunAll is synchronous in one scan -- next scan should have results.
    # Wait a few scans to be safe.
    time.sleep(0.2)

    passed  = oapp.read_value("GVL.fbMsgPackTests.uiPassed")
    failed  = oapp.read_value("GVL.fbMsgPackTests.uiFailed")
    first   = oapp.read_value("GVL.fbMsgPackTests.sFirstFail")
    summary = oapp.read_value("GVL.fbMsgPackTests.sSummary")
    done    = oapp.read_value("GVL.fbMsgPackTests.bDone")

    print("")
    print("  bDone       :", done)
    print("  summary     :", summary)
    print("  uiPassed    :", passed)
    print("  uiFailed    :", failed)
    print("  sFirstFail  :", first)
    print("")

    # Clear the trigger so next run is clean.
    write_bool("GVL.bRunMsgPackTests", False)

    # Rough pass/fail gate for the template's exit code.
    try:
        failed_n = int(str(failed).split("#")[-1])
    except Exception:
        failed_n = -1
    if failed_n == 0:
        print("RESULT: all green")
    else:
        print("RESULT: {} failure(s) -- see sFirstFail. "
              "Expected failures on today's code: T06, T07, T08, T09 "
              "(regressions for msgpack.md Fix 1/2/3).".format(failed_n))
finally:
    try:
        oapp.logout()
    except Exception:
        pass
