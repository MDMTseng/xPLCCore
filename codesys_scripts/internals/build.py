# -*- coding: ascii -*-
# IronPython script executed inside CODESYS via:
#   CODESYS.exe --profile="..." --noUI --runscript=build.py --scriptargs="<project_path> [<log_path>]"
#
# Exits with code 0 if build produced no errors, 1 if errors were found,
# 2 on script-level failure (bad args, can't open project, etc.).
#
# Results are both printed to the CODESYS log and appended to <log_path>
# so we can read them back from the parent shell.

from __future__ import print_function
import sys
import os
import traceback

# scriptengine globals: projects, system, online are auto-injected by CODESYS.

# Paths are hardcoded for now. --scriptargs parsing differs per CODESYS version,
# so to keep the smoke test deterministic we bypass it. Once we confirm the
# scripting API works, we can re-introduce argv parsing.
project_path = r"C:\Users\X1\Desktop\XPack2_codesys\PackerX.project"
log_path     = r"C:\Users\X1\Desktop\X2.5\TCP_UI\TCP_UI\codesys_scripts\build.log"

# Diagnostic: dump whatever CODESYS gave us in sys.argv so we learn the format.
try:
    print("[codesys-build] sys.argv = " + repr(list(sys.argv)))
except Exception:
    pass

log_lines = []

def emit(line):
    log_lines.append(line)
    print(line)

emit("[codesys-build] project: " + project_path)

proj = None
rc = 0
try:
    proj = projects.open(project_path)
    emit("[codesys-build] project opened")

    # Find every Application in the project and generate code for each.
    # generate_code() returns True on success. Errors go to the message store.
    found_app = False
    try:
        apps = proj.find("Application", True)
    except Exception:
        apps = []

    if apps:
        for app in apps:
            found_app = True
            emit("[codesys-build] generate_code: " + app.get_name())
            try:
                ok = app.generate_code()
                emit("[codesys-build]   -> " + ("OK" if ok else "FAILED"))
                if not ok:
                    rc = 1
            except Exception as ex:
                emit("[codesys-build]   -> exception: " + str(ex))
                rc = 1

    if not found_app:
        emit("[codesys-build] no Application objects found; building at project level")
        try:
            # Older/newer API variants - try build() and fall back.
            if hasattr(proj, "build"):
                proj.build()
            elif hasattr(proj, "clean_all"):
                proj.clean_all()
        except Exception as ex:
            emit("[codesys-build] project.build() exception: " + str(ex))
            rc = 1

    # Dump build messages. API is system.get_message_categories() +
    # system.get_message_objects(category).
    emit("[codesys-build] ---- messages ----")
    seen = 0
    try:
        cats = system.get_message_categories()
        emit("[codesys-build] {} message categories".format(len(list(cats))))
        for cat in system.get_message_categories():
            try:
                desc = system.get_message_category_description(cat)
            except Exception:
                desc = "?"
            try:
                msgs = list(system.get_message_objects(cat))
            except Exception as ex:
                emit("[codesys-build] cat {} ({}): get_message_objects failed: {}".format(
                    cat, desc, ex))
                continue
            if not msgs:
                continue
            emit("[codesys-build] category: {} ({}) -- {} msgs".format(
                desc, cat, len(msgs)))
            # Diagnostic on first message of first non-empty category
            if seen == 0 and msgs:
                m0 = msgs[0]
                attrs = [a for a in dir(m0) if not a.startswith("_")]
                emit("[codesys-build]   dir(msg[0]): " + ", ".join(attrs))
            for m in msgs:
                seen += 1
                sev = getattr(m, "severity", None)
                txt = getattr(m, "text", None) or getattr(m, "description", None) or str(m)
                pos = getattr(m, "position", "") or ""
                emit("  [{}] {} {}".format(sev, txt, pos))
                if sev is not None and "error" in str(sev).lower():
                    rc = 1
        emit("[codesys-build] total messages: {}".format(seen))
    except Exception as ex:
        emit("[codesys-build] message enumeration failed: " + str(ex))
        emit(traceback.format_exc())

except Exception:
    emit("[codesys-build] FATAL:")
    emit(traceback.format_exc())
    rc = 2

finally:
    try:
        if proj is not None:
            # Close without saving to avoid polluting the project file
            proj.close()
    except Exception:
        pass

# Flush to log file so the parent process can read it
if log_path:
    try:
        with open(log_path, "w") as f:
            for line in log_lines:
                f.write(line + "\n")
    except Exception as ex:
        print("[codesys-build] could not write log: " + str(ex))

emit("[codesys-build] exit code: " + str(rc))
sys.exit(rc)
