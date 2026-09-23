# -*- coding: ascii -*-
# Connect to the PLC at DEVICE level and list its exposed directories.
#
#   rpc.py exec --readonly --file jobs/templates/plc_files.py
#
# This is the connection the IDE's Communication Settings page makes. It
# does NOT log in to the application, download, start or stop anything.
#
# Only read operations are used: connect, get_file_list_of_directory,
# disconnect. IScriptOnlineDevice also exposes delete_file,
# delete_directory, rename_file, download_file (host -> PLC) and
# reset_origin. Those change the controller and are deliberately not
# called from here -- if you extend this script, keep it that way and
# put write operations in a separate, obviously-named job.

import traceback

# Placeholders a CODESYS runtime commonly exposes. Which of them resolve
# depends on the target's [SysFile] configuration, so probe rather than
# assume.
CANDIDATES = [
    ".",
    "/",
    "$PlcLogic$",
    "$PlcConfig$",
    "$visu$",
    "$hardwareconfig$",
]


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


def t(v):
    try:
        if isinstance(v, unicode):
            return v.encode("utf-8", "replace")
        return str(v)
    except Exception:
        return "?"


proj = projects.primary
dev = None
for c in proj.get_children():
    try:
        if c.is_device and c.get_name() == "Device":
            dev = c
            break
    except Exception:
        pass

if dev is None:
    p("PLC device node not found")
    raise SystemExit(1)

od = online.create_online_device(dev)

p("connecting (device level, no application login)...")
try:
    od.connect()
except Exception:
    p("connect failed:")
    p(traceback.format_exc())
    raise SystemExit(1)

try:
    p("connected: %s" % od.connected)

    for remote in CANDIDATES:
        p("")
        p("=" * 62)
        p("dir: %s" % remote)
        p("=" * 62)
        try:
            entries = list(od.get_file_list_of_directory(remote))
        except Exception as ex:
            p("  <%s>" % t(ex)[:140])
            continue
        if not entries:
            p("  (empty)")
            continue
        for e in entries:
            name = t(getattr(e, "name", e))
            size = getattr(e, "size", None)
            isdir = getattr(e, "is_directory", None)
            kind = "dir " if isdir else "file"
            if size is None:
                p("  %s  %s" % (kind, name))
            else:
                p("  %s  %10s  %s" % (kind, size, name))
finally:
    try:
        od.disconnect()
        p("")
        p("disconnected")
    except Exception:
        p("disconnect failed (harmless):")
        p(traceback.format_exc())
