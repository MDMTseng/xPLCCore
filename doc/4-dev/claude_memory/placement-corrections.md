---
name: placement-corrections
description: What each camera correction is for when placing a part in the tape (tape hole mainly X; bottom camera = pickup offset); both hole axes use the top camera mm/px
metadata:
  node_type: memory
  type: project
  originSessionId: f66f7e0c-0330-4b23-97fe-13374412ecbb
  modified: 2026-09-24T20:04:52.326Z
---

Owner, 2026-09-25:
- **Top-camera tape hole (`locHole`)** corrects the carrier tape's displacement. By design the tape mostly has an **X** positioning problem, but the owner decided to keep correcting **both X and Y** from the hole.
- **Bottom camera** corrects the offset of the part on the nozzle from the pickup (`armOffset` in the cycle).

Before 2026-09-25 the hole Y was scaled with the *bottom* camera's mm/px (the wrong camera). Both hole axes now use the hole's own (top camera) `locHole.mmpp`. See lib/production/judge.ts.

**How to apply:** scale anything measured in the top-camera image with the top camera's mm/px. If placements shift in Y on the machine after this fix, the old code was applying a wrongly scaled Y. Compare with the previous behavior before blaming the tape.
