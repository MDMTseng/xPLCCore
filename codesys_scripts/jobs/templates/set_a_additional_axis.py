# -*- coding: ascii -*-
# NOT IN USE (2026-09-30): the real PLC hangs on the first blended G1 with
# A as additional axis on SM3 4.20; the machine runs SM3 4.18 + Kin_CAxis.
# The PLC sources for it are in git d62b891. See
# doc_review/axes_setup_2026-09-29.md "Rollback".
#
# Make the A rotation an additional axis of the axis group SpiderR instead
# of its tool kinematics (Kin_CAxis), and put A in degrees.
#
#   - SpiderR: ToolKinematics removed, SM_Drive_GenericDSP402 mapped as
#     additional axis 0 (native export -> edit -> import, replace).
#   - SM_Drive_GenericDSP402 scaling: 6400 steps (one motor turn) = 360 u,
#     so 1 u = 1 degree (was 36 u: the /10 wrap trick for Kin_CAxis' +-180).
#   - Limits in degrees, the same physical values as before (288 u/s at
#     10 deg/u = 2880 deg/s, 8 turns/s).
#
# Needs SM3_Robotics >= 4.20 (MC_MoveLinearAbsolute.AdditionalAxes, see
# set_sm3_420.py) and the PLC code without A_AXIS_KIN_WRAP_SCALE. Run
# logged out. Idempotent. Never touches the delta arms.

import os
import re
import tempfile

AXIS = "SM_Drive_GenericDSP402"
PARAMS = {
    "ScalingUnits": "360",
    "iRatioTechUnitsNum": "9",          # 360 / 6400 = 9 / 160
    "dwRatioTechUnitsDenom": "160",
    "fSWMaxVelocity": "2880",           # deg/s    = 8 turns/s
    "fSWMaxAcceleration": "43200",      # deg/s^2  = 120 turns/s^2
    "fSWMaxDeceleration": "43200",
    "fSWMaxJerk": "864000",             # deg/s^3
}

from System import Array

proj = projects.primary


def t(v):
    try:
        if isinstance(v, unicode):
            return v.encode("utf-8", "replace")
        return str(v)
    except Exception:
        return "?"


# --- axis group -----------------------------------------------------------
groups = proj.find("SpiderR", True)
if len(groups) != 1:
    raise SystemExit("expected one SpiderR, found %d" % len(groups))
tmp = os.path.join(tempfile.gettempdir(), "SpiderR_addA.export")
proj.export_native(groups, tmp, recursive=True)
f = open(tmp, "rb")
xml = f.read()
f.close()

tool = re.compile(r'<Single Name="ToolKinematics".*?\n        </Single>\r?\n', re.S)
xml, n_tool = tool.subn('<Null Name="ToolKinematics" />\n', xml)
empty_map = '<List2 Name="AdditionalAxisMapping" />'
full_map = ('<List2 Name="AdditionalAxisMapping">\n'
            '            <Single Type="string">%s</Single>\n'
            '          </List2>' % AXIS)
n_map = xml.count(empty_map)
xml = xml.replace(empty_map, full_map)
print("SpiderR: tool kinematics removed %d, additional axis mapped %d" % (n_tool, n_map))
if AXIS not in xml.split('Name="AdditionalAxisMapping"', 1)[-1]:
    raise SystemExit("additional axis mapping not in the export -- nothing imported")
if n_tool or n_map:
    f = open(tmp, "wb")
    f.write(xml)
    f.close()

    class H(NativeImportHandler):
        def conflict(self, name, existingObject, newObject):
            return NativeImportResolve.replace

        def progress(self, name, obj, exception):
            pass

        def skipped(self, name):
            print("skipped", name)

    app = proj.find("Application", True)[0]
    app.import_native(Array[str]([tmp]), None, H())
    print("SpiderR replaced")
else:
    print("SpiderR already has A as additional axis")

# --- the A axis: scaling and limits ---------------------------------------
if AXIS in ("EAxis0", "EAxis1", "EAxis2"):
    raise SystemExit("REFUSED: the delta arms are not set here")
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
                        done.append(n)
                        print("%s %-22s %s -> %s" % (AXIS, n, old, t(prm.value)))
    except Exception:
        pass
    try:
        for c in o.get_children():
            scan(c)
    except Exception:
        pass


for top in proj.get_children():
    scan(top)
missing = [n for n in PARAMS if n not in done]
if missing:
    print("NOT FOUND:", missing)
proj.save()
print("saved")
