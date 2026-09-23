# -*- coding: ascii -*-
# Fetch the runtime's configuration files from the PLC.
#
#   rpc.py exec --readonly --file jobs/templates/plc_fetch_cfg.py
#
# Device-level connection only: no application login, no download, no
# start/stop. upload_file() reads FROM the controller (remote first,
# local second); download_file() would write to it and is not used.
#
# CODESYSControl.cfg carries the [SysCom] section, which is where COM
# port numbers are mapped onto the target's actual device nodes. A
# Modbus master configured for ComPort 3 talks to nothing at all if 3 is
# not mapped, and every setting in the project still looks correct --
# which is the first thing to rule out when a serial bus is silent.
#
# Files land in <state_dir>/plc/.

import os
import traceback

WANTED = ["CODESYSControl.cfg", "SysFileMap.cfg", "3S.dat"]
OUTDIR = os.path.join(config.state_dir(), "plc")


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


if not os.path.isdir(OUTDIR):
    os.makedirs(OUTDIR)

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
p("connecting (device level)...")
od.connect()

try:
    p("connected: %s" % od.connected)
    p("target dir: %s" % OUTDIR)
    p("")
    for name in WANTED:
        local = os.path.join(OUTDIR, name)
        try:
            od.upload_file(name, local, True)
            size = os.path.getsize(local) if os.path.isfile(local) else -1
            p("  fetched  %-24s %d bytes" % (name, size))
        except Exception as ex:
            # A refused transfer still leaves the local file created and
            # empty, which later reads as "fetched, but blank". Remove it
            # so a failure looks like a failure.
            try:
                if os.path.isfile(local) and os.path.getsize(local) == 0:
                    os.remove(local)
            except OSError:
                pass
            p("  FAILED   %-24s %s" % (name, str(ex).replace(chr(10), " ")[:90]))
finally:
    try:
        od.disconnect()
        p("")
        p("disconnected")
    except Exception:
        p(traceback.format_exc())
