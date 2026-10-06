---
name: machine-motion-safety
description: "Which PackerX axes may move during tests -- delta must stay virtual, A rotation and reel are safe"
metadata:
  node_type: memory
  type: project
  originSessionId: f66f7e0c-0330-4b23-97fe-13374412ecbb
  modified: 2026-10-05T16:00:31.389Z
---

Standing permission from the owner for work on the PackerX machine (PLC 192.168.1.70):

- **Delta arm trio EAxis0/1/2 must be virtual** (`bVirtual = TRUE`) whenever I download or run motion. "You have all the permission to do everything as long as delta bot motors are all virtual" (2026-09-23). `download_start_virtual.py` refuses otherwise.
- **EAXIS_A (drive 6, the Z-rotation `A` of `G1` / group SpiderR) is real and safe to rotate** -- owner, 2026-09-24: "A軸轉非常安全". Virtual cycle runs may drive it.
- **reelpullmotor (drive 8, tape) is real**; it moves only on `ReelGo`.

- **Exception, 2026-09-30:** the owner OK'd the delta real for the home-switch bench test ("ok開始"), step by step, with the owner at the machine. Done at run time (`tools/joint_bench.py real`: GVL.AxisSimConfigMask 0 + SET_AXIS_SIM 0; a PLC restart makes it virtual again). Each move is announced and waits for the owner's go.
- 2026-09-30 afternoon: many real-delta runs with the owner at the machine recording the ASDA scope. Once I started a run while the owner had already left ("我現在不在現場無法錄"); I aborted it (`plc_send_many_abort`) and powered off. **Before each real-delta run, ask whether the owner is at the machine** -- an earlier "ok" to a plan does not mean they are still there.
- 2026-09-30 night: the owner allowed remote real-delta runs ("現場是安全的"). This holds only for runs they OK explicitly.
- Since 2026-10-01 the tools enforce this: anything that moves the real delta refuses without `--owner-ok` (or XPLC_OWNER_OK=1), via `tools/machine.py`. Pass the flag only after the owner OK'd that run. Deploy with `tools/safe_install.py`, which powers off and checks the delta first.
- Since f771c6a (2026-10-05, on the sim only until downloaded) the PLC itself gates DIRECT, DRV_SDO write, DELTA_MODE real, JOINT_MOVE on a real axis and raised axis limits behind `SYS MAINT_ARM`; `machine.sys_cmd` arms it only in a run with `--owner-ok`. Code changes now go through `tools/deploy.py` (owner's sequence), each download asked first ([[ask-before-every-download]]).
- The A rotation is SM_Drive_GenericDSP402 (drive 7, QEC axis 2); EAXIS_A (drive 6) has nothing wired.

**Why:** the delta arms are the only motion that can crash into something; the owner has cleared the rest.

**How to apply:** check the delta trio is virtual before every download or motion run (the job does); don't ask again about moving EAXIS_A or the reel. Re-confirm if the mechanics change. Related: [[reply-in-traditional-chinese]].
