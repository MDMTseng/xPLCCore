# -*- coding: ascii -*-
# Save project, run generate_code, then login with Try so CODESYS does an
# online change. Use when deltas are small and additive (retain preserved,
# app keeps running). For invasive changes, use download_and_start.py.
proj = projects.primary
print("project:", proj.path)

try:
    proj.save()
    print("project saved")
except Exception as ex:
    print("save warning:", ex)

app_obj = None
for app in list(proj.find("Application", True) or []):
    app_obj = app; break
if app_obj is None:
    print("ERROR: no Application"); raise SystemExit(1)

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
    if "build" not in desc.lower():
        continue
    for m in system.get_message_objects(cat):
        if "error" in str(getattr(m, "severity", "")).lower():
            errs += 1
            print("  [ERR]", getattr(m, "text", str(m)))
if errs > 0:
    print("ABORT: build errors"); raise SystemExit(1)
print("build: errors=0")

oapp = online.create_online_application(app_obj)
try:
    oapp.login(OnlineChangeOption.Try, False)
    print("logged in (online change via Try)")
    try:
        oapp.start()
        print("application start() ok")
    except Exception as ex:
        # Harmless if app was already running; log and continue.
        print("start note:", str(ex)[:160])
    # Sanity read of a known GVL symbol.
    try:
        print("read GVL.bVirtualMotorsMode_Request =",
              oapp.read_value("GVL.bVirtualMotorsMode_Request"))
        print("read GVL.bVirtualMotorsMode         =",
              oapp.read_value("GVL.bVirtualMotorsMode"))
    except Exception as ex:
        print("post-login read warning:", ex)
finally:
    try:
        oapp.logout()
    except Exception:
        pass
print("done.")
