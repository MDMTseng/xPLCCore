# CLAUDE.md -- standing instructions for this repo

PackerX: a delta-robot tape-and-reel packer. CODESYS PLC program
(`codesys_code/`, Structured Text, SoftMotion, EtherCAT), Electron + React
operator UI (`PluginHello.tsx`, `components/`, `lib/`), Python tools that
drive both (`tools/`, `codesys_scripts/`). New PC: `doc/4-dev/new_pc_setup.md`.
Where the work stands: `doc_review/HANDOVER_2026-10-06.md`.
Memory of past sessions: `doc/4-dev/claude_memory/` (install with
`tools/install_claude_memory.py`).

## Talking to the owner

- Every user-visible reply in **Traditional Chinese (Taiwan)**. Code,
  identifiers, repo docs and commit messages stay English.
- Say what a long step does and how long it takes before starting it
  (downloads, homing, soaks); report results plainly, including failures.
- The owner is often away from the machine: ask before anything that needs
  someone at it.

## Machine safety (non-negotiable)

- The delta arms (EAxis0/1/2) stay **virtual** unless the owner OKs a
  real-delta run; tools that move the real delta need `--owner-ok`. Ask
  whether the owner is at the machine before each real-delta run.
- **Every full download to the real PLC needs the owner's go**, through
  `tools/deploy.py` (no motion -> Error -> stop + reset -> import/build ->
  download -> wait -> start -> bus check). Drives off. One retry at most,
  then stop and report. PLC changes go to the local soft PLC first.
- EtherCAT SyncOffset <= 50. Protective settings (filters, limits) are
  tested on first, removed only after a clean protected run.
- PLC maintenance gate: DIRECT, DRV_SDO writes, DELTA_MODE real, JOINT_MOVE
  on a real axis and raised axis limits need `SYS MAINT_ARM`;
  `machine.sys_cmd` arms it only in a run with the owner's OK.

## Working rules

- ST: `AND` / `OR` do not short-circuit -- pointer guards as nested IFs (a
  first-cycle NULL deref kills the EtherCAT task).
- Tool timeouts default 30 s; longer only with measured step times.
- Detect stalls by each phase's typical time, not blanket timeouts.
- Soak reports: one artifact per campaign, updated every 30 min, format in
  `doc/4-dev/claude_memory/soak-report-format.md`
  (`tools/soak_report/gen.py`).
- Git: work branch `flow-analysis`; merge into `main` with `--no-ff`; the
  pre-push hook runs `npm run check` (needs node on PATH; in Git Bash
  `export PATH="/c/Program Files/nodejs:$PATH"`) and `git lfs pre-push`.
  Never `--no-verify`, never force-push `main` without asking.
- Open work: `doc_review/TODO.md` -- tick items off there with the commit.

## Where things are

| Area | Path |
|---|---|
| PLC sources (truth) | `codesys_code/Application` |
| CODESYS project + devices + libraries (LFS) | `codesys_project/PackerX.projectarchive`; sim: `PackerX_sim.project` |
| CODESYS daemon / deploy | `codesys_scripts/rpc.py`, `tools/deploy.py`, `codesys_scripts/jobs/templates/` |
| Machine helpers | `tools/machine.py`, `tools/xplc.py`, `tools/topology.py` |
| Soaks / bus | `tools/circle_soak.py`, `tools/soak_segments.py`, `tools/bus_watch.py`, `tools/soak_report/` |
| Sim scene | `tools/sim/run_virtual.py` |
| Protocol | `lib/protocol.ts`, `doc/2-contracts/protocol.md` |
| Reviews / handovers | `doc_review/` |
