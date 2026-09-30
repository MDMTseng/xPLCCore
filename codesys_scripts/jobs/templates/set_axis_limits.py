# -*- coding: ascii -*-
# Axis dynamic limits (the axis' "Dynamic limits": fSWMax*), from the peaks
# the production flow reached on the machine (2026-09-29, real flow with
# the vision mock, plus a queued-replay run for the worst segment):
#
#   delta joints (deg at the joint, 31:1): peaks 946-1084 deg/s,
#     62 300-78 500 deg/s^2, ~1e7 deg/s^3. The planner is conservative
#     with joint limits: 1100 deg/s + 80 000 deg/s^2 cost 3-5 % although
#     no joint reached them. So: 1160 deg/s (the ASDA-B3's ~6000 rpm), 100
#     000 deg/s^2, 1e7 deg/s^3 -- the same motion time as before (was 100
#     000 / 800 000 / 1e7: no limit).
#   group A (1 u = 10 deg, the /10 wrap trick in the scaling): x1.5 (time
#     scale: v x 1.5, a x 2.25, j x 3.375) of the former 288 u/s, 4320
#     u/s^2, 86 400 u/s^3 -> 432 / 9720 / 291 600 u = 4320 deg/s (720
#     rpm), 97 200 deg/s^2. The owner found x2 to be where A starts losing
#     steps (Motors page, driver current 0.62 A by its hardware switch,
#     2026-09-30); x1.5 needs 56 % of that torque.
#   reel (mm): the production move TAPE.REEL_MOVE, 5000 mm/s, 100 000
#     mm/s^2, 1e5 mm/s^3.
#
# Only limits: scalings and virtual modes are left as they are.

LIMITS = {
    "EAxis0": {"fSWMaxVelocity": "1160", "fSWMaxAcceleration": "100000", "fSWMaxDeceleration": "100000", "fSWMaxJerk": "10000000"},
    "EAxis1": {"fSWMaxVelocity": "1160", "fSWMaxAcceleration": "100000", "fSWMaxDeceleration": "100000", "fSWMaxJerk": "10000000"},
    "EAxis2": {"fSWMaxVelocity": "1160", "fSWMaxAcceleration": "100000", "fSWMaxDeceleration": "100000", "fSWMaxJerk": "10000000"},
    "SM_Drive_GenericDSP402": {"fSWMaxVelocity": "432", "fSWMaxAcceleration": "9720", "fSWMaxDeceleration": "9720", "fSWMaxJerk": "291600"},
    "reelpullmotor": {"fSWMaxVelocity": "5000", "fSWMaxAcceleration": "100000", "fSWMaxDeceleration": "100000", "fSWMaxJerk": "100000"},
}

proj = projects.primary


def t(v):
    try:
        if isinstance(v, unicode):
            return v.encode("utf-8", "replace")
        return str(v)
    except Exception:
        return "?"


seen = {}


def scan(o):
    try:
        name = t(o.get_name())
        if o.is_device and name in LIMITS:
            for c in o.connectors:
                try:
                    prms = list(c.host_parameters)
                except Exception:
                    continue
                for prm in prms:
                    n = t(prm.name)
                    if n in LIMITS[name]:
                        old = t(prm.value)
                        prm.value = LIMITS[name][n]
                        seen.setdefault(name, []).append("%s %s -> %s" % (n, old, t(prm.value)))
    except Exception:
        pass
    try:
        for c in o.get_children():
            scan(c)
    except Exception:
        pass


for top in proj.get_children():
    scan(top)
for name in LIMITS:
    print(name, "|", "; ".join(seen.get(name, ["NOT FOUND"])))
