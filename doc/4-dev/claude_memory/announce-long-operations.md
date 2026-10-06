---
name: announce-long-operations
description: "Before a long PLC operation (download, homing, long run) tell the owner what it is and how long it takes; don't hide progress behind `| tail`"
metadata:
  node_type: memory
  type: feedback
  originSessionId: f66f7e0c-0330-4b23-97fe-13374412ecbb
  modified: 2026-10-01T05:51:57.580Z
---

On 2026-10-01 the owner interrupted a `safe_install.py` download because nothing showed for a while and they could not tell whether I was stuck.

**Why:** the owner watches from the machine; silent multi-minute tool calls look like hangs, and interrupting a download mid-transfer is risky (see [[plc-download-pitfalls]]).

**How to apply:** say in one line what is starting and its typical duration (download ~1 min, homing ~20 s, runs as planned) before the call. Prefer output that streams, or split long chains (download, then homing) into separate calls so each finishes visibly. See [[stall-detection-by-typical-timing]].
