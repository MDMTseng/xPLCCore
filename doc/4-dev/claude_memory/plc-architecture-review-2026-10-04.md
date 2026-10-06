---
name: plc-architecture-review-2026-10-04
description: "PLC/host architecture review done 2026-10-04; safety fixes + UnInited log fix DEPLOYED (2026-10-05 01:42); remaining refactor order in doc_review; group 11000 after a failed start = download again"
metadata:
  node_type: memory
  type: project
  originSessionId: f66f7e0c-0330-4b23-97fe-13374412ecbb
  modified: 2026-10-04T09:05:51.132Z
---

2026-10-04: four agent reviews of the PLC program and host tools, written up
in xPLCCore/doc_review/plc_architecture_review_2026-10-04.md. Safety fixes
(40c0773 + 3e8e2b9: DIRECT test writes once, skip-home only on the drives'
own virtual flag, no motion packet in the scan that left Ready, REEL_RESUME
one-step wrap, reply packer checks the slot before writing) are **deployed**
since 17:04; verified with the delta virtual (overflow drop clean, DIRECT
abort, FSM to Ready). Machine left UnInited, delta virtual and off.

**Why:** owner asked for a refactor/API review and said "ok" / "go" to the
safety fixes and the download.

**How to apply:**
- Not yet verified: GA_EV 7 with a real delta, REEL_RESUME, DIRECT abort
  with the drive enabled -- do them when the owner is at the machine.
- If GroupEnabling fails with SMC 11000 (and SMC_GroupDisable too) right
  after a download whose start failed: download again, that cures it.
- PLC log: machine.save_plc_log works again since 2026-10-05 (it had a
  relative job path and silently returned the stale file); the logger is a
  normal 500-entry ring. UnInited no longer floods it (deployed 01:42).
- Program on the PLC since 2026-10-05 03:36: HEAD of that time (safety
  fixes, UnInited log fix, GVL cleanup + CONSTANT block, PRG_DiagReply).
  SYS ESP_ODD and EC_STATS e_amin/e_amax/e_apeak no longer exist.
- Diagnostic SYS commands (EC_STATS, DWELL, SETTLE*, DEM_*, ESP_*,
  FB_STATS, TASK_STATS, VERSION, GET_DIAG) are answered by PRG_DiagReply in
  the Comm task (TCP_MSGPAK_Server routes them); a new read-only diag
  command belongs there, not in DrainHostPackets.
- Host client done 2026-10-05: tools/xplc.py (Machine + UiRelay/Direct,
  typed errors); machine.py's link functions delegate to it. Not yet: the
  plc_direct tools moved to Machine(Direct()), machine.py helpers folded in.
- Dead variables / CONSTANT and diagnostics-to-Comm done 2026-10-05. Next
  in the agreed order: command table + CASE dispatch, split AxisGroupSM,
  Stopping state (on machine); GVL split deferred (renames GVL.* symbols
  that daemon job templates read). Error-state stop
  behaviour and first-error latch are known, unfixed.
Related: [[st-and-no-short-circuit]], [[asda-stale-target-investigation]],
[[plc-download-pitfalls]], [[machine-motion-safety]].
