# -*- coding: ascii -*-
# After adding EAXIS_A as Auxiliary Linear under SpiderR, verify online
# that the axis group sees 4 coordinate channels (X/Y/Z/A) and that A
# tracks the EAXIS_A actual position 1:1 (no /10 scaling, no wrap).
#
# Pre-req: project built+downloaded with the new SpiderR config.

EXPRESSIONS = [
    # Group dimensionality
    "AxisGroupGVL_agSpiderR.SpiderR.iAxesCount",
    # Last commanded / actual MCS pose
    "AxisGroupSM.ReadPosition.c.X",
    "AxisGroupSM.ReadPosition.c.Y",
    "AxisGroupSM.ReadPosition.c.Z",
    "AxisGroupSM.ReadPosition.c.A",
    # Direct EAXIS_A actual to compare
    "EAXIS_A.fActPosition",
]

import json
out = {}
for e in EXPRESSIONS:
    try:
        v = online.read_value(e)
        out[e] = v
    except Exception as ex:
        out[e] = "ERR: " + str(ex)

print(json.dumps(out, indent=2, default=str))
