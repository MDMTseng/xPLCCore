---
name: delta-shock-settle-method
description: "Owner's standard for judging delta drive settings -- shock on the round (circle-like) path, settling on PnP stops"
metadata:
  node_type: memory
  type: feedback
  originSessionId: f66f7e0c-0330-4b23-97fe-13374412ecbb
  modified: 2026-10-01T17:13:19.651Z
---

Judge drive settings (filters, gains) for the delta with two separate tests (owner, 2026-10-02):
- **Shock**: the round path -- 4-point square +-50 mm at Z 0 with corner blend 49 mm (nearly a circle), 70 %, continuous. Smooth path, so torque-change spikes come from the stale targets. Metric: FB_STATS torque change per cycle (max, count >= 20 %).
- **Settling**: PnP stops -- X +-50 with dips to Z -15, exact stops, 70 %. Metric: SYS SETTLE, time to stay within 0.2 mm (motor-encoder TCP, not the tool tip).

**Why:** the square-with-dips path mixes real path accelerations into the shock numbers; a circle isolates the stale-target spikes. Settling only matters at stops.

**How to apply:** `tools/param_sweep.py` runs both per level. Related: [[asda-stale-target-investigation]].
