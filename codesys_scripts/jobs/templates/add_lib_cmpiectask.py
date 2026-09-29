# -*- coding: ascii -*-
# Add the CmpIecTask system library to the Application's Library Manager
# (task statistics for SYS TASK_STATS: IecTaskGetFirst/Next, IecTaskGetInfo3,
# IecTaskResetStatistics). Idempotent. Run logged out.

LIB = "CmpIecTask, 3.5.21.0 (System)"

proj = projects.primary


def t(v):
    try:
        return str(v)
    except Exception:
        return "?"


done = False
for lm in proj.find("Library Manager", True):
    try:
        path = t(lm.parent.get_name())
    except Exception:
        path = "?"
    if path != "Application":
        continue
    have = False
    for r in lm.references:
        if "CmpIecTask" in t(getattr(r, "name", r)):
            have = True
    if have:
        print("already referenced in", path)
    else:
        lm.add_library(LIB)
        print("added", LIB, "to", path)
    done = True
if not done:
    print("no Application Library Manager found")
try:
    proj.save()
    print("saved")
except Exception as ex:
    print("save ex:", ex)
