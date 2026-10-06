---
name: plc-exception-remote-recovery
description: PLC app stuck in exception (warm reset hangs) can be recovered remotely via the Intewell RTOS telnet shell by resuming the frozen IEC task
metadata:
  node_type: memory
  type: reference
  originSessionId: f66f7e0c-0330-4b23-97fe-13374412ecbb
  modified: 2026-09-30T00:34:49.772Z
---

The PackerX PLC (192.168.1.70) is a Kyland Intewell RTOS VM (vm1) with an open, login-free telnet shell on port 23 (`help`, `task`, `tc`, `tp`, `tr`/`ts`/`td` resume/suspend/stop task, `cpuuse`, `deadlock`; no reboot command; files under /nfsd/codesys, no runtime log file).

After an IEC exception (operation_state 0x21, later 0x10421 with the reset flag stuck), `o.reset(ResetOption.Warm)` from the IDE hangs ~290 s and does nothing. In `task`, the faulting IEC task has frozen TICKS. Other IEC tasks show `S` too, which is their normal state while the app is stopped. Recovered 2026-09-25 with `tr <EtherCAT_Task id>`. The task exited and was recreated, operation_state went to 1, then `o.start()` worked.

**Why:** the owner is often away from the machine and cannot power-cycle it.

A second hang mode exists. Ping still answers but every TCP port (23 telnet, 1217, 11740, 8125/8126) times out. The telnet shell is gone too, so only a power cycle helps. This happened 2026-09-29 22:57, right after installing SM3 4.20 (A as additional axis), during 5 queued Cor 45 G1s with A. Reproduced 2/2 (2026-09-30 08:32): the first blended G1 with A as additional axis on SM3 4.20 kills the whole OS within ~2 s; the Windows sim does not reproduce it. `tools/intewell_watch.py` logs the task table.

**How to apply:** try this before telling the owner a power cycle is needed. Script: scratchpad-style socket client sending `cmd\r\n` to 192.168.1.70:23. A warm reset loses the event ring, so save post-mortem data first. Related: [[plc-download-pitfalls]], [[machine-motion-safety]].
