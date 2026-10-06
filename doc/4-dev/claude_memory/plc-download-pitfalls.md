---
name: plc-download-pitfalls
description: "How a PLC full download wedges the app in \"exit\" state (needs power cycle) and how to avoid it"
metadata:
  node_type: memory
  type: project
  originSessionId: f66f7e0c-0330-4b23-97fe-13374412ecbb
  modified: 2026-10-05T11:41:33.384Z
---

A full download of a *running* PLC app with `download_start_virtual.py` (OnlineChangeOption.Never) hung for 376 s and left the app with operation_state 0x101 (program loaded + **exit** flag). Every later start fails with "Operation aborted, the application is in read-only mode". This happened twice (the first time around 2026-09-23/24, killed mid-download; again 2026-09-24 13:36 when a task-config change added PRG_EventLog). Cold reset and reset origin did not clear it; only a PLC **power cycle** did. The user has to do that physically.

**Why:** an app stuck in exit blocks every download and start, and the machine stays stopped until someone is at the PLC.

**How to apply:**
- For layout or task-config changes, use `jobs/templates/stop_then_install.py`, which stops the app first. Check the delta arms are virtual in the project before running it.
- Watch a download against its typical time (~20 s) and investigate after ~60 s instead of waiting. See [[stall-detection-by-typical-timing]].
- Diagnose with `operation_state` after a Keep login: bit 0x100 means exit, which means a power cycle is needed.
- The daemon retires at its 4 h session budget. Check uptime and restart it proactively before a long job.

- 2026-09-25: an older `rpc.py push --no-online` still ran the regression stage (`virtual_motors_force.py`, login with `OnlineChangeOption.Try`). After a layout change (`FlyEventBufferSize` 10 -> 32) that login downloaded the running app; the IDE hung 10 min and the RTOS stopped answering entirely (no ping, telnet, 11740) until a power cycle. Fixed since: `push --no-online` now only imports, builds and saves (no login, no regression).
- **Real-PLC install that worked (2026-09-29, after the power cycle):** real daemon (no XPLC_CONFIG, port 7420) -> `set_comm_task_period.py` -> `create_*` method jobs (logged out) -> `push --no-online` (build 0 errors) -> check `bVirtual` of EAxis0/1/2 with `dump_ethercat.py` -> `install --on-site`: download 19.4 s, back in RUN, scans ticking, FSM UnInited, no error. A download wipes the RETAIN plan (BootEpochCount back to 1); the renderer re-sends the plan at RUN.
- **Download only with the drives off.** 2026-09-30: an `install --on-site` while the real delta was servo-on left EtherCAT dead afterwards. `xConfigFinished` stayed FALSE and every slave stayed in BOOT. Cold reset, bus restart and re-installing the last good build did not help; a PLC power cycle did. Before an install, bring the FSM to UnInited and check `bRegulatorOn` is FALSE. Since d94e352 the FSM really powers off in UnInited.
  - Same symptom 2026-10-05 19:36 WITH the drives off (UnInited, axes 0, delta virtual): first download took 81.5 s (usual 20-26 s), then no "Networkadapter opened" in the PLC log, slaves BOOT, telnet `ifconfig` en1 (EtherCAT NIC, link up) tx/rx counters frozen. A 2nd download and an xRestart pulse did not help. Sign to look for: download much slower than usual.
- **EtherCAT master SyncOffset >= 60 wedges the PLC's EtherCAT layer.** 2026-09-30: SyncOffset 70 reached OP, then within ~1 min the master / NIC (I210_2) died. Later downloads logged no EtherCAT startup at all, and a scan got "SysEthernet: packet could not be sent, error code:20". Downloads and cold resets do not recover it; a PLC restart does (the drives are fine). Stay at 50. The PLC log (IDE Device -> Log) shows this layer. Read it remotely with `jobs/templates/read_plc_log.py` (writes `jobs/plc_log_PlcLog.txt`). It holds only 500 entries, so read it **before** retrying downloads, or the first error is pushed out.
- Treat an array-size or CONSTANT change as a layout change: stop_then_install, not online change, and only when the owner can reach the PLC.

- **No scripting login is read-only.** Measured on the local Control Win sim (2026-09-25, CODESYS 3.5.22, running app):
  - When the code differs, Keep, Try and Force all online-change it. Never stops the app and downloads.
  - When the device config differs (for example an axis bVirtual), even Keep stops the app and downloads.
  - The daemon now enforces this in `codesys_scripts/plc_guard.py`. It fingerprints the project and refuses any keep-mode login unless the project matches the last deploy fingerprint (`jobs/deployed_fp.json`).
  - Deploys go through `rpc.py push` (online change, after `layout_check`) or `rpc.py install --on-site` (download).
- The local sim is `XPLC_CONFIG=codesys_scripts/codesys_env.sim.json`: SimPrj copy, Control Win x64 via Gateway-2, all axes virtual, EtherCAT_Task watchdog off, runtime user management off. See [[local-softplc-sim]].

Related: [[machine-motion-safety]], [[plc-exception-remote-recovery]].
