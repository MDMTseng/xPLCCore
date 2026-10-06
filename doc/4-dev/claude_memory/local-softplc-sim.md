---
name: local-softplc-sim
description: How the PC-local CODESYS Control Win x64 simulation of PackerX is set up and started (no machine needed)
metadata:
  node_type: memory
  type: project
  originSessionId: f66f7e0c-0330-4b23-97fe-13374412ecbb
  modified: 2026-10-05T16:07:57.780Z
---

Built 2026-09-25, while the real PLC (192.168.1.70) was down and the owner was remote.

- **Project:** `C:/Users/PC/Desktop/XPack2_codesys/SimPrj/PackerX_sim.project`, a copy of NewPrj/PackerX.project.
  - Device changed to CODESYS Control Win V3 x64 (4096 / "0000 0004" / 3.5.22.30), reached through gateway "Gateway-2" at 127.0.0.1.
  - Every axis is set bVirtual TRUE.
  - EtherCAT_Task watchdog is off. The EtherCAT master fails to open (no Npcap), which is expected.
- **Daemon config:** `codesys_scripts/codesys_env.sim.json` (gitignored). Port 7421, state in `codesys_scripts/jobs_sim`. Every rpc.py, supervisor.py and run_virtual command needs `XPLC_CONFIG` set to it.
- **Runtime:** Windows service "CODESYS Control Win V3 - x64" (Start-Service). In its config file (`C:\Windows\System32\config\systemprofile\AppData\Roaming\CODESYS\CODESYSControlWinV3x64\26DDB80B\CODESYSControl.cfg`), `SECURITY.UserMgmtEnforce=NO` was uncommented, with a backup at `.bak-before-sim`. Without it a login pops an "Add Device User" dialog that blocks scripting.
  - The runtime runs in demo mode (SoftMotion demo, time limited); restart the service when it expires.
  - The log is `PlcLog.csv` in the same folder.
- **Harness:** `run_virtual.py --plc 127.0.0.1` (vision_mock then uses 127.0.0.1:8126).
- **Dialog watcher:** a dialog/click helper (`dlg.ps1`) lived in the session scratchpad. It enumerates CODESYS windows via user32 and can press a button by label.

- **2026-10-05 update:** the sim project needs, after every import, a stand-in GVL `GVL_SimEsp` (ecat_esp_in0..31 / out0..15 USINT; no EasyCAT slave in the sim) and the AxisGroupSM `TransitionParameter` array sized by `SMC_RCNST.MAX_TRANS_PARAMS` (older SM3); the scratchpad jobs `sim_esp_gvl.py` / `sim_patch_trans.py` do it (`deploy.py --post-import`). After each download write `GVL.SimNoFieldbus := TRUE`, or the EtherCAT supervisor sends the FSM to Error. Switch: `rpc.py stop`, `supervisor.py kill --yes`, Start-Service, `XPLC_CONFIG=<abs path> rpc.py daemon-start`, UI `connect_tcp {host:127.0.0.1}`. Never HOME_GO on the sim: the homing FB fails (fault Homing:homing_fb) and leaves the virtual axes in a pose (arm Z ~74) where every G1 trips GroupErrorStop until a re-download; the UI skips homing by itself when axes_sim_mask has the delta virtual (lib/homing.ts, 0034b17). The harness now needs its token (`codesys_scripts/jobs/harness_token`, read by run_virtual.push).

**Why:** the owner wants to test PLC-side changes and the deploy tooling without touching the machine.

**How to apply:** only one CODESYS should run at a time, because `supervisor.py kill` kills every CODESYS.exe. Switch back to the real config (unset XPLC_CONFIG) only when the real PLC is reachable. See [[plc-download-pitfalls]].
