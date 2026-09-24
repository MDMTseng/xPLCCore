# -*- coding: ascii -*-
# Set the device-tree "virtual mode" (host parameter bVirtual) of one or
# more SoftMotion axes, then save the project. Offline edit only: the PLC
# keeps running the old configuration until a full download
# (download_start_virtual.py).
#
#   rpc.py exec --file jobs/templates/set_axis_virtual.py
#
# Edit AXES below. Setting TRUE is always safe; setting a delta arm
# (EAxis0/1/2) FALSE makes it a real drive -- this script refuses that.

AXES = {"reelpullmotor": True}
DELTA_AXES = ("EAxis0", "EAxis1", "EAxis2")


def t(v):
    try:
        if isinstance(v, unicode):
            return v.encode("utf-8", "replace")
        return str(v)
    except Exception:
        return "?"


for name, want in AXES.items():
    if name in DELTA_AXES and not want:
        raise SystemExit("REFUSED: %s must stay virtual" % name)

proj = projects.primary
done = {}


def scan(o):
    try:
        name = t(o.get_name())
        if o.is_device and name in AXES:
            for c in o.connectors:
                try:
                    prms = list(c.host_parameters)
                except Exception:
                    continue
                for prm in prms:
                    if t(prm.name) == "bVirtual":
                        old = t(prm.value)
                        prm.value = "TRUE" if AXES[name] else "FALSE"
                        done[name] = (old, t(prm.value))
    except Exception:
        pass
    try:
        for c in o.get_children():
            scan(c)
    except Exception:
        pass


for top in proj.get_children():
    scan(top)

for name in AXES:
    if name in done:
        print("%-14s bVirtual %s -> %s" % (name, done[name][0], done[name][1]))
    else:
        print("%-14s ** bVirtual parameter not found **" % name)
if len(done) == len(AXES):
    proj.save()
    print("saved")
