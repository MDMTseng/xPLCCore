---
name: tool-timeouts-30s
description: Default command timeouts to 30 s; longer only with clear evidence of what the step needs
metadata:
  node_type: memory
  type: feedback
  originSessionId: f66f7e0c-0330-4b23-97fe-13374412ecbb
  modified: 2026-10-02T02:17:05.868Z
---

Set tool / command timeouts to 30 s by default (owner, 2026-10-02). Use a longer one only with clear evidence the step needs it, sized from that evidence.

**Why:** the owner watches the commands (also from the phone); long timeouts like `timeout 600` look like the session may hang, and they hide a stuck step for minutes.

**How to apply:** measured durations to size the exceptions:
- CODESYS job (`rpc.py exec`): 2-20 s;
- build: ~10 s;
- `push --no-online`: ~40-50 s;
- download (`safe_install.py`): 15-65 s + ~20 s EtherCAT start;
- homing to Ready: ~20 s.

Say the expected duration before a long step (see [[announce-long-operations]]); background runs get a timeout matching their planned length plus a margin, not a blanket hour. Related: [[stall-detection-by-typical-timing]].
