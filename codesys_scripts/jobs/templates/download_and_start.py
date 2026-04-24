# -*- coding: ascii -*-
# Save project, login to PLC with download (force), start the
# application. Assumes motors are disabled / safe to run.

import time

proj = projects.primary
print("project:", proj.path)

# 1. Save project so disk == IDE state
try:
    proj.save()
    print("project saved")
except Exception as ex:
    print("save warning:", ex)

# 2. Locate Application
app_obj = None
for app in list(proj.find("Application", True) or []):
    app_obj = app; break
if app_obj is None:
    print("ERROR: no Application"); raise SystemExit(1)

# 3. Build first
try:
    system.clear_messages("{97f48d64-a2a3-4856-b640-75c046e37ea9}")
except Exception:
    pass
app_obj.generate_code()
errs = 0
for cat in system.get_message_categories():
    try:
        desc = system.get_message_category_description(cat)
    except Exception:
        desc = str(cat)
    if "build" not in desc.lower(): continue
    for m in system.get_message_objects(cat):
        sev = str(getattr(m, "severity", ""))
        if "error" in sev.lower():
            errs += 1
            print("  [ERR]", getattr(m, "text", str(m)))
if errs > 0:
    print("ABORT: build errors"); raise SystemExit(1)
print("build: errors=0")

# 4. Create online app
oapp = online.create_online_application(app_obj)

# 5. Probe which login options this build exposes
opts = [a for a in dir(OnlineChangeOption) if not a.startswith("_")]
print("OnlineChangeOption values:", opts)

# 6. Login. Force-download preferred so new code is guaranteed present.
logged_in = False
for name in ["ForceDownload", "None", "NONE", "Download", "LoginWithDownload"]:
    if name not in opts: continue
    try:
        opt = getattr(OnlineChangeOption, name)
        oapp.login(opt, False)
        print("logged in via", name)
        logged_in = True
        break
    except Exception as ex:
        print("  login try", name, "->", str(ex)[:120])

if not logged_in:
    # Fall back to Try (may refuse if code changed significantly)
    try:
        oapp.login(OnlineChangeOption.Try, False)
        print("logged in via Try (no force)")
        logged_in = True
    except Exception as ex:
        print("ERROR: all login attempts failed:", ex)
        raise SystemExit(1)

# 7. Start application
try:
    oapp.start()
    print("application started")
except Exception as ex:
    print("start failed:", ex)
    try: oapp.logout()
    except Exception: pass
    raise SystemExit(1)

# 8. Verify running
time.sleep(0.3)
try:
    # Read a known GVL to confirm we're live
    val = oapp.read_value("GVL.bRunMsgPackTests")
    print("post-start read GVL.bRunMsgPackTests =", val)
except Exception as ex:
    print("post-start read warning:", ex)

oapp.logout()
print("done. PLC is running; you can now run `run_msgpack_tests`.")
