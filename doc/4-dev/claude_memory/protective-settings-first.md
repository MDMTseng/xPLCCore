---
name: protective-settings-first
description: "When removing a protective setting (drive filter etc.), test WITH it first, remove it only after the protected run is clean"
metadata:
  node_type: memory
  type: feedback
  originSessionId: f66f7e0c-0330-4b23-97fe-13374412ecbb
  modified: 2026-10-02T09:04:17.357Z
---

When a test sequence compares a protective setting against none (e.g. the delta drives' P1.068 12 ms filter vs no filter), run the protected level first; drop the protection only after that run shows the problem is gone.

**Why:** the owner, 2026-10-02: the filter protects the delta's gearboxes from torque shocks; I had ordered `param_sweep.py --levels P1.068=0 base`, so the unfiltered run came before anything confirmed the fix.

**How to apply:** order sweep levels from most to least protective; same for speed (slow before fast) and gains. Related: [[machine-motion-safety]], [[delta-shock-settle-method]].
