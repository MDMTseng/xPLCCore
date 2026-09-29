# -*- coding: ascii -*-
# SUPERSEDED by set_a_additional_axis.py (A as additional axis, 1 u = 1
# deg). Kept for the record; running it now would set degree limits ten
# times too low.
#
# The axis group's A axis (SpiderR tool kinematics Kin_CAxis):
# SM_Drive_GenericDSP402, drive ID 7, logical device 1 = the second axis
# of the QEC stepper driver (M2 / "Y", where the rotation motor is wired).
# Sets virtual mode and the dynamic limits. Units: 1 u = 10 degrees (the
# axis scaling 6400 steps = 36 u implements the /10 wrap trick, so the
# kinematics' +-180 covers +-1800 real degrees).
#
# The motor is an open-loop stepper (no encoder): too much speed or
# acceleration loses steps silently. Start low, raise after a step-loss
# test. Refuses to touch the delta arms.

AXIS = "SM_Drive_GenericDSP402"
PARAMS = {
    "bVirtual": "FALSE",
    "fSWMaxVelocity": "36",        # u/s    = 360 deg/s, one turn per second
    "fSWMaxAcceleration": "360",   # u/s^2  = 3600 deg/s^2
    "fSWMaxDeceleration": "360",
    "fSWMaxJerk": "3600",          # u/s^3  = 36000 deg/s^3
}

if AXIS in ("EAxis0", "EAxis1", "EAxis2"):
    raise SystemExit("REFUSED: the delta arms are not set here")

proj = projects.primary


def t(v):
    try:
        if isinstance(v, unicode):
            return v.encode("utf-8", "replace")
        return str(v)
    except Exception:
        return "?"


done = []


def scan(o):
    try:
        if o.is_device and t(o.get_name()) == AXIS:
            for c in o.connectors:
                try:
                    prms = list(c.host_parameters)
                except Exception:
                    continue
                for prm in prms:
                    n = t(prm.name)
                    if n in PARAMS:
                        old = t(prm.value)
                        prm.value = PARAMS[n]
                        done.append("%-20s %s -> %s" % (n, old, t(prm.value)))
    except Exception:
        pass
    try:
        for c in o.get_children():
            scan(c)
    except Exception:
        pass


for top in proj.get_children():
    scan(top)
for d in done:
    print(AXIS, d)
missing = [n for n in PARAMS if not any(d.startswith(n + " ") or d.startswith(n) for d in done)]
if missing:
    print("NOT FOUND:", missing)
