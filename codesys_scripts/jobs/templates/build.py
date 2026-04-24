# -*- coding: ascii -*-
# build job -- run inside the watcher. Reuses the already-open project,
# generates code for every Application, and dumps Build-category messages.

import traceback

PROJECT = r"C:\Users\X1\Desktop\XPack2_codesys\PackerX.project"

proj = projects.primary
if proj is None:
    print("[build] opening project: " + PROJECT)
    proj = projects.open(PROJECT)

print("[build] project: " + proj.path)

# Clear prior build-category messages so we only see this run's output.
BUILD_CAT = "{97f48d64-a2a3-4856-b640-75c046e37ea9}"
try:
    system.clear_messages(BUILD_CAT)
except Exception:
    pass

overall_ok = True
try:
    apps = list(proj.find("Application", True) or [])
except Exception as ex:
    apps = []
    print("[build] find(Application) failed: " + str(ex))

if not apps:
    print("[build] WARNING: no Application objects found")

for app in apps:
    try:
        name = app.get_name()
    except Exception:
        name = "<app>"
    try:
        ok = app.generate_code()
        # generate_code returns False when nothing had to be regenerated --
        # that is NOT a build failure. Rely on the Build-category messages
        # for the real verdict.
        print("[build] generate_code({}) -> {}".format(name, ok))
    except Exception as ex:
        overall_ok = False
        print("[build] generate_code({}) -> exception: {}".format(name, ex))

# Real verdict: enumerate messages in the Build category.
print("[build] ---- build messages ----")
errors = 0
warnings = 0
try:
    for cat in system.get_message_categories():
        try:
            desc = system.get_message_category_description(cat)
        except Exception:
            desc = str(cat)
        # Only surface the Build category (and anything flagged as error
        # in other categories).
        msgs = list(system.get_message_objects(cat))
        if not msgs:
            continue
        for m in msgs:
            sev = str(getattr(m, "severity", ""))
            txt = getattr(m, "text", None) or str(m)
            pos = getattr(m, "position_text", "") or ""
            is_build = "build" in desc.lower()
            is_err = "error" in sev.lower()
            is_warn = "warning" in sev.lower()
            if is_build or is_err or is_warn:
                print("  [{}] [{}] {} {}".format(desc, sev, txt, pos))
            if is_err:
                errors += 1
            if is_warn:
                warnings += 1
except Exception:
    print("[build] message enumeration failed:")
    print(traceback.format_exc())
    overall_ok = False

print("[build] summary: errors={} warnings={} overall={}".format(
    errors, warnings, "OK" if (overall_ok and errors == 0) else "FAIL"))
