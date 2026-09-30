# -*- coding: ascii -*-
# NOT IN USE (2026-09-30): the real PLC hangs on the first blended G1 with
# A as additional axis on SM3 4.20; the machine runs SM3 4.18 + Kin_CAxis.
# See doc_review/axes_setup_2026-09-29.md "Rollback".
#
# Resolve the SoftMotion (SM3_*) library placeholders of the Application to
# the newest installed versions (SoftMotion 4.20: SM3_Robotics 4.20.1.0, the
# drive libraries 4.19). Needed for additional axes in the axis group
# (MC_MoveLinearAbsolute.AdditionalAxes); 4.18 has none.
#
# Redirecting the top-level placeholders is not enough: the nested ones
# (SM3_RBase, SM3_Math, ...) stay at the library profile's 4.18 and the
# build breaks with type mismatches. They are added as placeholders of the
# Application and redirected too. Idempotent. Run logged out, then build.

import os

M = r"C:\ProgramData\CODESYS\Managed Libraries\CODESYS"
NESTED = ["SM3_RBase", "SM3_Math", "SM3_Error", "SM3_CommonPublic", "SM3_Shared", "SM3_RCP",
          "SM3_ETC_ITF", "SM3_StringUtils", "SM3_CPKernelDefaults", "SM3_Drive_CiA_DSP402"]

proj = projects.primary


def t(v):
    try:
        return str(v)
    except Exception:
        return "?"


def newest(lib):
    d = os.path.join(M, lib)
    if not os.path.isdir(d):
        return None
    vs = sorted(os.listdir(d), key=lambda v: [int(x) for x in v.split(".")])
    return "%s, %s (CODESYS)" % (lib, vs[-1])


found = False
for lm in proj.find("Library Manager", True):
    if t(getattr(lm.parent, "get_name", lambda: "")()) != "Application":
        continue
    found = True
    have = dict((t(r.placeholder_name).lower(), r) for r in lm.references if r.is_placeholder)
    for pn in NESTED:
        if pn.lower() not in have and newest(pn):
            lm.add_placeholder(pn, newest(pn))
    for r in lm.references:
        if not r.is_placeholder:
            continue
        pn = t(r.placeholder_name)
        if not pn.upper().startswith("SM3_"):
            continue
        target = newest(pn)
        if target is None:
            print(pn, "not installed -- left at", t(r.effective_resolution))
            continue
        if t(r.effective_resolution) != target:
            r.set_redirection(target)
        print("%-32s %s" % (pn, t(r.effective_resolution)))
if not found:
    raise SystemExit("no Application Library Manager")
proj.save()
print("saved")
