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
#   A (the group's additional axis, 1 u = 1 deg since 2026-09-29): 2880
#     deg/s = 8 turns/s, 43 200 deg/s^2. Production reached 92-97 % of
#     them (then 288 u/s, 4320 u/s^2 at 10 deg/u) -- kept.
#   reel (mm): the production move TAPE.REEL_MOVE, 5000 mm/s, 100 000
#     mm/s^2, 1e5 mm/s^3.
#
# Only limits: scalings and virtual modes are left as they are.

LIMITS = {
    "EAxis0": {"fSWMaxVelocity": "1160", "fSWMaxAcceleration": "100000", "fSWMaxDeceleration": "100000", "fSWMaxJerk": "10000000"},
    "EAxis1": {"fSWMaxVelocity": "1160", "fSWMaxAcceleration": "100000", "fSWMaxDeceleration": "100000", "fSWMaxJerk": "10000000"},
    "EAxis2": {"fSWMaxVelocity": "1160", "fSWMaxAcceleration": "100000", "fSWMaxDeceleration": "100000", "fSWMaxJerk": "10000000"},
    "SM_Drive_GenericDSP402": {"fSWMaxVelocity": "2880", "fSWMaxAcceleration": "43200", "fSWMaxDeceleration": "43200", "fSWMaxJerk": "864000"},
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
