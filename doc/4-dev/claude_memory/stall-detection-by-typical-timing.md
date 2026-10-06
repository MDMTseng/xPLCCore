---
name: stall-detection-by-typical-timing
description: "Detect stuck runs from each phase's measured typical duration, not long blanket timeouts (e.g. 300 s)"
metadata:
  node_type: memory
  type: feedback
  originSessionId: f66f7e0c-0330-4b23-97fe-13374412ecbb
  modified: 2026-09-24T03:30:04.100Z
---

When running long jobs (virtual scene, tests, PLC downloads), detect a stall from each phase's typical duration plus a margin and stop at once with diagnostics. Do not wrap everything in a blanket `timeout 300` and wait it out.

**Why:** the user watched runs sit "stuck" for minutes (2026-09-24: "try to note down common wait time and use that time to check it get stuck in the future, not 300s everytime").

**How to apply:** `tools/sim/run_virtual.py` has the phase limits (UI up 45 s, links 20 s, Ready 40 s, first tape check 25 s, between checks 12 s) and exits with code 2 plus diagnostics on a stall. Typical timings measured 2026-09-24: full virtual run ~40 s for 10 checks, import_all ~10 s, online change ~15 s, full download ~20 s, queue_test ~25 s. Size shell timeouts from these, and when a step overruns, check its state before waiting any longer. Related: [[machine-motion-safety]].
