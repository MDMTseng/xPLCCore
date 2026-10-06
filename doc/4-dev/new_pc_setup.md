# Setting up a new development PC (2026-10-06)

Everything needed to continue on another Windows PC is in this repo; the
only things that are not are the installers (CODESYS, Node, Python) and the
machine itself. Steps in order; each ends with a check.

## 1. Install

| What | Version used | Notes |
|---|---|---|
| Git for Windows + **Git LFS** | Git 2.x, LFS 3.7 | the CODESYS project files are in LFS |
| Node.js | 24.19.0 | UI, tests, the pre-push hook |
| Python | 3.12.10 | tools/, codesys_scripts/ |
| CODESYS | **3.5.22.30 (V3.5 SP22 Patch 3), 64-bit**, English UI | the scripts look for `C:/Program Files/CODESYS 3.5.22.30/CODESYS/Common/CODESYS.exe` and profile `CODESYS V3.5 SP22 Patch 3` |
| CODESYS SoftMotion | 4.18.0.0 (the project's) | add-on; the archive in step 3 brings the libraries and device descriptions the project uses |
| CODESYS Control Win V3 x64 + Gateway V3 | with CODESYS | only for the local soft-PLC sim (step 6) |
| PlatformIO (VS Code) | optional | only to rebuild the EasyCAT ESP32 firmware (`firmware/easycat_esp32`) |
| Claude Code | current | optional; step 7 |

Check: `git lfs version`, `node --version`, `python --version`, CODESYS
starts.

## 2. Clone

```
git lfs install
git clone https://github.com/MDMTseng/xPLCCore.git <workspace>\xPLCCore
cd <workspace>\xPLCCore
git checkout flow-analysis          # the work branch (main is a fast-forward of it as of 2026-10-06)
npm ci
pip install -r requirements.txt
```

Keep a parent folder (`<workspace>`, e.g. `D:\dev\codesys_dev`): Claude
Code is started there (step 7).

Check: `npm run check` (typecheck + 136 vitest tests) passes;
`codesys_project\PackerX.projectarchive` is 81 435 416 bytes, not a
130-byte LFS pointer (if it is: `git lfs pull`).

If the clone stops with "Clone succeeded, but checkout failed" (the 81 MB
LFS download broke off, seen once on 2026-10-06), clone without the LFS
files first and fetch them separately:

```
set GIT_LFS_SKIP_SMUDGE=1
git clone --branch flow-analysis https://github.com/MDMTseng/xPLCCore.git xPLCCore
set GIT_LFS_SKIP_SMUDGE=
cd xPLCCore
git lfs pull
```

## 3. CODESYS project

The PLC project is `codesys_project/PackerX.projectarchive` (Git LFS):
the project plus every device description (ASDA-B3-E, QEC-R11MP3S,
EC0808DN, EasyCAT PRO, ...) and library (SM3 4.18, ...) it references.

1. CODESYS: File -> Project Archive -> Extract Archive -> the file above;
   accept installing the devices and libraries it offers.
2. Save the project where you like, e.g. `D:\codesys\NewPrj\PackerX.project`
   (outside the repo: CODESYS writes compile caches next to it).
3. Close CODESYS (the daemon opens it itself).

The ST sources of truth are `codesys_code/` (imported by the tooling);
device tree, tasks and libraries live only in the project file -- refresh
the archive after changing them (`rpc.py exec --readonly --file
jobs/templates/save_archive.py`, commit the new archive).

## 4. Machine-local config

```
copy codesys_scripts\codesys_env.sample.json codesys_scripts\codesys_env.json
```

Edit `project` (the .project path from step 3) and `plc_host`
(`192.168.1.70`). The file is git-ignored.

UI calibration: `standalone/data/` is git-ignored (per-instance data);
copy the machine's calibration in:

```
mkdir standalone\data\standalone
copy config\standalone\calib.json standalone\data\standalone\calib.json
```

## 5. Network and first contact with the machine

- The PC's Ethernet port on the machine network: static `192.168.1.100 /
  255.255.255.0`. PLC: `192.168.1.70` (Kyland Intewell RTOS VM "vm1";
  CODESYS runtime; app TCP 8125, vision 8126; RTOS shell telnet 23, no
  login).
- `ping 192.168.1.70`, `python tools/plc_telnet.py task` (read-only).
- Start the CODESYS daemon and check: 
  ```
  python codesys_scripts\rpc.py daemon-start
  python codesys_scripts\rpc.py doctor
  ```
  If the IDE cannot reach the device (first time on this PC): open the
  project in the IDE, Device -> Communication Settings -> Scan Network,
  select the PLC, save, close; then `daemon-start` again.
- After a fresh checkout `plc_guard` has no record of what the PLC runs:
  the first keep-mode login is refused. Only if you KNOW the PLC runs this
  project: `python codesys_scripts\rpc.py mark-synced --i-know`. Otherwise
  deploy (step 8 rules).
- UI with the harness (what every tool drives):
  ```
  set XPLC_HARNESS=1
  node standalone\run.cjs
  python codesys_scripts\internals\remote_harness.py      (second shell; writes codesys_scripts/jobs/harness_token)
  ```
  In the UI connect the PLC (or `python -c "import sys; sys.path.insert(0,'tools'); import machine as mc; mc.reconnect(); print(mc.fsm())"`).

Check: FSM `UnInited`, `python tools\bus_watch.py --owner-ok --hours 0.01`
reports all 7 slaves OP.

## 6. Local soft-PLC sim (optional, no machine needed)

1. Start the Windows service "CODESYS Control Win V3 - x64". In its
   `CODESYSControl.cfg` (under
   `C:\Windows\System32\config\systemprofile\AppData\Roaming\CODESYS\CODESYSControlWinV3x64\<id>\`)
   uncomment `SECURITY.UserMgmtEnforce=NO`, restart the service.
2. Copy `codesys_project\PackerX_sim.project` (LFS) somewhere, copy
   `codesys_scripts\codesys_env.sim.sample.json` to `codesys_env.sim.json`
   and set its `project`.
3. `set XPLC_CONFIG=<abs path>\codesys_env.sim.json`, then `rpc.py
   daemon-start`. The sim project lags the real one and needs, after each
   import: `jobs/templates/sim_esp_gvl.py` (once) and
   `jobs/templates/sim_patch_trans.py`; after each download write
   `GVL.SimNoFieldbus := TRUE` (`rpc.py write GVL.SimNoFieldbus TRUE`). Never
   HOME_GO on the sim (the UI skips homing by itself when the delta is
   virtual). Details: doc/4-dev/claude_memory/local-softplc-sim.md.
4. `python tools\sim\run_virtual.py --plc 127.0.0.1 --plan "3,-2,4" --chaos 3 --reapply-at-stop`
   (vision and feeder mocks included) ends with the reel books check.

## 7. Claude Code

Start Claude Code in `<workspace>` (the repo's parent). Its memory of
this project (the owner's rules, the machine's history) is versioned in
`doc/4-dev/claude_memory/`; install it once:

```
python tools\install_claude_memory.py              (copies into ~/.claude/projects/<slug>/memory)
```

and refresh the repo copy before leaving a PC: `python
tools\install_claude_memory.py --export`, commit. `CLAUDE.md` at the repo
root holds the standing instructions.

## 8. Rules that carry over

- Replies to the owner in Traditional Chinese; repo docs and commits in
  English.
- The delta trio stays virtual unless the owner OKs a real-delta run
  (`--owner-ok`); downloads only with the owner's go, through
  `tools/deploy.py`, drives off; SyncOffset <= 50.
- Merge work branches into `main` with `--no-ff`.
- Open work: `doc_review/TODO.md`. Latest reviews:
  `doc_review/plc_ui_integration_review_2026-10-05.md`,
  `doc_review/plan_counting_audit_2026-10-06.md`,
  `doc_review/ethercat_dropout_2026-10-03.md`.
