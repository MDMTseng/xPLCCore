---
name: st-and-no-short-circuit
description: "CODESYS ST AND/OR evaluate every operand -- never put p^.x next to p <> 0 in one condition; a NULL deref in the first cycle kills the EtherCAT task silently (AxisGroupSMScans=1, task gone from the telnet table)"
metadata:
  node_type: memory
  type: feedback
  originSessionId: f66f7e0c-0330-4b23-97fe-13374412ecbb
  modified: 2026-10-04T08:53:57.181Z
---

CODESYS ST `AND` / `OR` evaluate all operands (no short circuit; `AND_THEN` /
`OR_ELSE` exist but the codebase uses nested IFs). 2026-10-04 I wrote
`IF (pSlot <> 0) AND (pStart >= pSlot^.buf) ...` in FB_MpPacker; the unbound
packer in MsgPakInfoInit dereferenced NULL in the first cycle. Symptoms:
install reports "download done" but the app does not start, `start()`
times out, `GVL.AxisGroupSMScans = 1`, `RuntimeMs = 0`, and the EtherCAT
task is *missing* from the Intewell `task` table (not suspended -- deleted).

**Why:** a production PLC; one bad pointer guard cost a failed download and
a dead application.

**How to apply:** pointer guards as nested IFs. Before any download, grep
the diff for `<> 0) AND` / `AND (p` patterns with `^` in the same line.
After a download, check `post-start state: run` (machine.safe_install does
since 3e8e2b9) and `GVL.AxisGroupSMScans` climbing. Related:
[[plc-exception-remote-recovery]], [[plc-download-pitfalls]].
