# -*- coding: ascii -*-
# READ-ONLY (writes one file outside the project).
#
#   rpc.py exec --readonly --file jobs/templates/export_modbus_xml.py
#
# Exports the Modbus subtree to PLCopen XML so the slave's CHANNEL list
# can be read.
#
# Why: a Modbus slave's read/write channels live in the device editor's
# own tab, not in the parameters that connectors expose, so dump_serial.py
# cannot see them. If that list is empty the master transmits nothing at
# all -- which looks exactly like a dead port, and is the cheapest of the
# candidate causes to rule out. The XML carries them.
#
# Writes to <state_dir>/modbus_export.xml.

import os

OUT = os.path.join(config.state_dir(), "modbus_export.xml")


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


def nm(o):
    try:
        n = o.get_name()
        return n.encode("utf-8", "replace") if isinstance(n, unicode) else str(n)
    except Exception:
        return "<unnamed>"


proj = projects.primary

target = None
stack = list(proj.get_children())
while stack:
    o = stack.pop(0)
    if nm(o) == "Modbus_COM":
        target = o
        break
    try:
        stack.extend(list(o.get_children()))
    except Exception:
        pass

if target is None:
    p("Modbus_COM not found")
    raise SystemExit(1)

p("exporting: %s" % nm(target))
p("to       : %s" % OUT)

try:
    if os.path.isfile(OUT):
        os.remove(OUT)
except Exception:
    pass

# export_xml(objects, path, recursive, export_folder_structure,
#            declarations_as_plaintext)
try:
    proj.export_xml([target], OUT, True, False, True)
    p("export returned")
except Exception as ex:
    p("export failed: %s" % ex)
    raise SystemExit(1)

if os.path.isfile(OUT):
    p("wrote %d bytes" % os.path.getsize(OUT))
else:
    p("NO FILE WRITTEN")
