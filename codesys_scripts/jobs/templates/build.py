# -*- coding: ascii -*-
# build job -- reuses the already-open project, generates code for every
# Application, and dumps Build-category messages.
#
# Two things this has to survive on a localised CODESYS (this machine runs
# a Chinese UI):
#
#   1. Output encoding. The daemon captures stdout through IronPython's
#      cStringIO, which raises UnicodeEncodeError on non-ascii. Build
#      messages and object names are not all ascii, so every print goes
#      through p() below.
#
#   2. Category matching. get_message_category_description() returns a
#      LOCALISED string, so the old `"build" in desc.lower()` test never
#      matched on a non-English UI and silently filtered out every build
#      message -- reporting a clean build no matter what. Match on the
#      category GUID instead, which is stable across languages.
#
#      Severity is safe to compare as text: it is a .NET enum, and its
#      ToString() yields the member name ("Error", "Warning"), not a
#      translated string.

import traceback

BUILD_CAT = "{97f48d64-a2a3-4856-b640-75c046e37ea9}"


def p(s):
    try:
        if isinstance(s, unicode):
            s = s.encode("utf-8", "replace")
        print(s)
    except Exception:
        try:
            print(repr(s))
        except Exception:
            pass


def safe(v):
    try:
        if isinstance(v, unicode):
            return v.encode("utf-8", "replace")
        return str(v)
    except Exception:
        return "?"


PROJECT = config.project_path()

proj = projects.primary
if proj is None:
    p("[build] opening project: " + PROJECT)
    proj = projects.open(PROJECT)

p("[build] project: %s" % safe(proj.path))

try:
    system.clear_messages(BUILD_CAT)
except Exception:
    pass

overall_ok = True
try:
    apps = list(proj.find("Application", True) or [])
except Exception as ex:
    apps = []
    p("[build] find(Application) failed: %s" % safe(ex))

if not apps:
    p("[build] WARNING: no Application objects found")

for app in apps:
    name = safe(getattr(app, "get_name", lambda: "<app>")())
    try:
        ok = app.generate_code()
        # generate_code returns False when nothing had to be regenerated.
        # That is NOT a build failure -- the messages decide.
        p("[build] generate_code(%s) -> %s" % (name, ok))
    except Exception as ex:
        overall_ok = False
        p("[build] generate_code(%s) -> exception: %s" % (name, safe(ex)))

p("[build] ---- build messages ----")
errors = 0
warnings = 0
shown = 0
MAX_SHOWN = 200

try:
    msgs = list(system.get_message_objects(BUILD_CAT))
except Exception:
    msgs = []
    p("[build] could not read the build category directly:")
    p(traceback.format_exc())
    overall_ok = False

for m in msgs:
    sev = safe(getattr(m, "severity", ""))
    txt = safe(getattr(m, "text", None) or m)
    pos = safe(getattr(m, "position_text", "") or "")
    low = sev.lower()
    if "error" in low:
        errors += 1
    elif "warning" in low:
        warnings += 1
    if shown < MAX_SHOWN:
        p("  [%s] %s %s" % (sev, txt, pos))
        shown += 1

if len(msgs) > MAX_SHOWN:
    p("  ... (%d more messages)" % (len(msgs) - MAX_SHOWN))

p("[build] summary: errors=%d warnings=%d total_messages=%d overall=%s" % (
    errors, warnings, len(msgs),
    "OK" if (overall_ok and errors == 0) else "FAIL"))
