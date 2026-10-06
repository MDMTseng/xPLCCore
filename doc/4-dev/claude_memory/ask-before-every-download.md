---
name: ask-before-every-download
description: "Ask the owner before EVERY full download to the real PLC, even mid-debug; one attempt, then stop and report. Owner: \"你好像常常弄到ethercat失效\""
metadata:
  node_type: memory
  type: feedback
  originSessionId: f66f7e0c-0330-4b23-97fe-13374412ecbb
  modified: 2026-10-05T14:02:04.264Z
---

Ask the owner before every full download (`safe_install` / `rpc.py install`)
to the real PLC 192.168.1.70, even when a debug task seems to imply it. Say
why a download is needed (layout change?) and whether the change could
avoid one. If the bus does not come up afterwards, make one more attempt at
most, then stop and report. Do not keep trying.

**Why:** 2026-10-05 19:36: I downloaded the stop-cause logging (GVL arrays)
without asking. The first install took 83.5 s instead of the usual 28 s,
right after the daemon restarted on its budget. EtherCAT never opened its
adapter (slaves BOOT, en1 counters frozen), and a power cycle was needed
while the owner could not do one. The owner says he never sees this in
manual CODESYS use, and that I often break EtherCAT. Tally of bus-dead
events after my actions: 09-30 (install with servo on), 10-04 (first-cycle
NULL deref, [[st-and-no-short-circuit]]), 10-05 19:36 (unknown).

**Owner's write sequence = `tools/deploy.py`** (a4c0391, run end to end on
the local sim only so far): no motion -> FSM Error (GA_EV 9) -> UI link
closed -> stop + warm reset (Keep login, so run it BEFORE importing; after a
daemon restart the guard fingerprint may need `rpc.py mark-synced --i-know`)
-> import + build -> download -> wait 5 s -> start -> xConfigFinished in the
same session. A download > 45 s is not started. Use it instead of
safe_install for code changes. The owner's own full downloads from the IDE
never break the bus, so the difference is in my sequence.

**How to apply:** batch PLC changes into as few downloads as possible.
Prefer debug instrumentation that needs no layout change. Do not install
right after a daemon (re)start; let a read job log in first. Related:
[[plc-download-pitfalls]], [[announce-long-operations]].
