# -*- coding: ascii -*-
#   rpc.py exec --file jobs/templates/sim_patch_trans.py   (XPLC_CONFIG = the sim config)
#   or: tools/deploy.py --allow-bus-down --post-import codesys_scripts/jobs/templates/sim_patch_trans.py
# SIM PROJECT ONLY: the sim's older SM3 has MAX_TRANS_PARAMS != 2; size the
# AxisGroupSM.TransitionParameter array by the library constant. Re-run after
# every import (import_all puts the [0..1] back). Then builds.
from System import Guid
BUILD = Guid("{97f48d64-a2a3-4856-b640-75c046e37ea9}")
app = projects.primary.active_application
pou = [o for o in app.find("AxisGroupSM", True) if hasattr(o, "textual_declaration")][0]
d = pou.textual_declaration.text
old = "TransitionParameter : ARRAY [0..1] OF LREAL := [0, 0];"
new = "TransitionParameter : ARRAY [0..SMC_RCNST.MAX_TRANS_PARAMS - 1] OF LREAL;"
if old in d:
    pou.textual_declaration.replace(d.replace(old, new))
    print("patched")
else:
    print("already patched" if new in d else "PATTERN NOT FOUND")
system.clear_messages(BUILD)
app.generate_code()
errs = [m for m in system.get_message_objects(BUILD) if str(m.severity).lower().find("error") >= 0]
for m in errs[:10]:
    print("  [ERR] " + str(m.text))
print("build errors: %d" % len(errs))
