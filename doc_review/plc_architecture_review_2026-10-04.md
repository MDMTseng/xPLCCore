# PLC and host architecture review, first fixes (2026-10-04)

Four read-only reviews of the PLC program (codesys_code/Application, ~12.8 k
lines of ST) and the host tools, one per area, then the safety findings
fixed in place. The protocol itself is sound; what is worth redesigning is a
single command schema shared by PLC, UI, Python and the docs. The rest is
incremental: safety first, then the 1 ms task, then splitting the big POUs.

## 1. Findings

### Safety (fixed in this round, section 2)

| # | Finding | Where |
|---|---|---|
| S1 | DIRECT drive test: state 99 (aborted) wrote the target and controlword taps of EAxis0 every cycle for as long as `DirEnable` stayed TRUE, i.e. until a host `stop` -- also after EAxis0 had become a real SoftMotion drive | PRG_EventLog.st DIRECT block |
| S2 | Skip-home (`EV_HOME_GO_FORCE_SKIP`) accepted the legacy `GVL.bVirtualMotorsMode`, a daemon-forced flag that never touches the drives: real, unhomed axes could reach Ready. The same flag was OR'd into the published `axes_sim_mask`, which the host's own checks trust (`machine.is_delta_virtual`) | Transition.st GroupEnabled; AxisGroupSM.st sim mask |
| S3 | Reply packer checked the slot capacity only *after* writing; a long string went up to ~258 bytes into the next slot (only the last row has a guard region). `EC_STATS` with `tr`/`obj` is close to the 1 KB slot | FB_MpPacker Pack*, MsgPakInfoWrapup |
| S4 | `REEL_RESUME` unwrapped the reel position with two uncapped WHILE loops, one turn per iteration, in the 1 ms task | DrainHostPackets.st |
| S5 | `ProcessFlyEventsAndIo` can raise EV_ERROR and `Transition` switches state at once, but `ProcessMotionPacket` still ran in the same scan and could accept a G1 after the error | AxisGroupSM.st end of body |
| S6 | Host: `sync_shift_sweep.virtual()` returned quietly after five failed tries (delta possibly still real); `joint_bench real/powered` had no owner gate; `soak_segments` had its own gate ignoring `XPLC_OWNER_OK`; `circle_soak` fell back to `DELTA_MODE real:0` without looking at the axis states | tools/ |

Not fixed, needs machine time (behaviour change):
- Error entry drops group power / disables every scan while the group stop
  is still running (AxisGroupManager/Update.st Error, AxisGroupSM.st
  ~1079): the real stop is the drive's own fault reaction. A `Stopping`
  state (stop to standstill or timeout, then power off) is the fix.
- Each new EV_ERROR overwrites `LastErrorSource/ID` (Transition.st): latch
  the first cause.
- Fault classes: a belt-window or Coord1 fault forces a full re-home like a
  drive fault does.

### Real time (1 ms EtherCAT task)

- `DrainHostPackets` handles up to 16 packets per scan in the EtherCAT task;
  the diagnostic replies are heavy (`EC_STATS` ~100 pack calls, `TASK_STATS`
  8x12 string keys plus runtime calls, `DEM_STATS snap` ~100 CONCATs on
  255-char strings). A burst of polls can overrun a scan.
- Every `TryRead*` walks the whole map from the start; some handlers read the
  same key two or three times; a motion packet waiting at the FIFO head is
  re-parsed every scan.
- PRG_EventLog: `SMC_GroupReadSetPosition` every scan, 3x EXPT/SQRT per
  stopped cycle, bursts of 300 SQRT + a 400x6 copy when a settle completes.
- Fix: answer the ~12 diagnostic commands from the Comm task (5 ms) through
  a second reply ring; until then allow one heavy diagnostic per scan.

### Structure

- `AxisGroupSM` ~1580 lines, ~250 instance variables, ~344 GVL symbols;
  motion, I/O, protocol replies, diagnostics, tape/reel and test hooks in
  one POU. Candidates to pull out verbatim, one per commit: `FB_ReelAxis`,
  `FB_FlyEventEngine`, `FB_TapeCycle`, `FB_Coord1Tracking`, `FB_SimMode`,
  `FB_JointBench`, `FB_MotionDiag`.
- `DrainHostPackets` ~1650-line ELSIF chain of string compares, a second
  chain in `ProcessMotionPacket`; the order-free SYS list in
  TCP_MSGPAK_Server must be kept in sync by hand. Fix: one command table
  (`CmdLookup(cmd) : E_HostCmd` + flags), parse `id`/`cmd`/version once in
  the Comm task, `CASE` dispatch into per-area methods; old names stay as
  aliases.
- Duplicates: motion-id offset clamp x3, `pin_op_seq` parser x2, TTL read
  x2, log pagination x6, FSM walk x6 on the host, event codes x5 on the host.
- Naming drift: UPPER_SNAKE / camelCase / G-code command names; `bad_arg`,
  `bad_args`, `bad_value`; `unknown SYS cmd` vs `unknown_cmd`; `ERROR_CODE`
  vs `err_id`; three reply shapes (CSV text, numbered keys, arrays). No
  numeric error codes; `protocol_version` optional and never echoed.
- GVL: 15 hard-coded %I/%Q taps (shifted silently at the slave reorder),
  stations 1004..1006 in PRG_EventLog, ~200 KB diagnostic arrays, dead
  variables (`EspOddCount`, `OddLog*`, `reMP_info_SIZE`,
  `COORD1_CAM_MAX_POINTS`), writable "constants" (`PROTOCOL_VERSION`).
  Cross-task reads without a handshake (Ev* ring, SimIo X/Y pair, reel
  position) -- acceptable but undocumented.
- Dead code: `WebServer_SIMPLE` (hard-coded 192.168.3.6), `VERSION`,
  `BLOCK_FOR_MOTION_STOP`, `BLOCK_FOR_REEL_STOP`, `EC_STATS tr/obj`.
  Twenty commands missing from doc/2-contracts/protocol.md.
- Host: five ways into the PLC (UI relay, plc_direct, IDE daemon, telnet,
  hand-rolled sockets in ~18 templates/tests); copied helpers (`log`,
  `to_ready`, rpc wrapper); errors matched by text; `SystemExit` as the
  library's error type; PLC IP hard-coded in ~15 tools although
  `config.plc_host()` exists.

### Recommended order

1. Safety fixes (this round) -- one deployment.
2. Dead variables out, true constants `VAR_GLOBAL CONSTANT`, GVL split into
   GVL_Comm / GVL_Sim / GVL_Machine / GVL_Diag -- layout change, one full
   download, batch it.
3. Host: shared safety module and topology file (started: tools/topology.py),
   one client class with UI-relay and direct transports, typed exceptions.
4. Diagnostics to the Comm task; command table + CASE dispatch.
5. Split AxisGroupSM, planner/executor in ProcessMotionPacket.
6. Stopping state and fault classes -- on the machine.
7. One YAML command schema generating the ST enum/lookup, protocol.ts, the
   Python helpers and protocol.md.

## 2. Changes in this round

PLC (codesys_code/Application):
- PRG_EventLog DIRECT: state 99 writes the drive once (only while
  `xDirAllowed`), clears `DirEnable`, stays 99 for the SYS DIRECT report;
  the stop branch no longer touches a state-99 run. GVL comment on the taps
  corrected.
- Transition GroupEnabled: skip-home only when
  `(GVL.AxisSimMaskApplied AND 16#07) = 16#07`, i.e. the drives themselves
  are virtual. AxisGroupSM: `GVL.AxisSimMask := GVL.AxisSimMaskApplied`
  (the legacy flag still feeds the *desired* mask, applied to the drives
  after a host SET_AXIS_SIM).
- AxisGroupSM: after `ProcessFlyEventsAndIo()`, return unless the FSM is
  still Ready.
- DrainHostPackets REEL_RESUME: one-step wrap with LREAL_TO_LINT.
- Reply packer: `MsgPakInfo.overflowed` (new struct field, cleared by
  Init/Clear); the primitives `PackString/DINT/LINT/REAL/Bool/MapHeader/
  ArrayHeader` check the bound slot *before* writing and flag it instead of
  writing; the PackKv*/PackPair*/PackElem* helpers also look at the latch;
  `MsgPakInfoWrapup` refuses an overflowed packet (and checks its own 'seq'
  write). Host effect: a too-long reply is dropped whole
  (`ReMpOverflowDropCount`), as before, but nothing is corrupted.

Deployment: the struct field changes the memory layout, so this needs a
full download (`tools/safe_install.py`, drives off), not an online change.

Step 2 of the plan, 2026-10-05 (dead variables and constants; the GVL
split is deferred because it renames `GVL.*` symbols that the daemon's job
templates read):
- Removed: `GVL.EspOddCount / EspOddDev / OddLogMs / OddLogDev / OddLogN`
  (never written since the ESP32 moved to the MCPWM capture) and the SYS
  `ESP_ODD` command that read them (always `n=0`; `tools/dem_events.py`
  keeps a stub); `GVL.EspArrMinUs / EspArrMaxUs / EspArrMaxPeak` (forced
  to 0) and the `e_amin / e_amax / e_apeak` keys of `EC_STATS` (the per-100
  ms ArrLog replaced them; `stale_suite.py` reads them with `.get`);
  `GVL.reMP_info_SIZE`; AxisGroupSM `GroupReadPositionFb2`, `DBG_BLOCK`,
  `TaskInstructionBufferSize`. About 6 KB less.
- `VAR_GLOBAL CONSTANT` (end of GVL.st): `PROTOCOL_VERSION`,
  `MAX_SLOT_PAYLOAD`, `VIRTUAL_MOTORS_TTL`, `AXIS_SIM_TTL_MS`,
  `BUS_RESTART_COOLDOWN_MS`, `UI_HEARTBEAT_TIMEOUT_MS`,
  `COORD1_MODE_LINEAR / TABLE`, `COORD1_CAM_MAX_POINTS` (now also the bound
  of the `Coord1Cam*` arrays). They can no longer be forced online.
- Build 0 errors / 98 warnings; full download (layout change).

Host (tools/):
- `topology.py` (new): PLC host/port from codesys_env.json, slave names and
  stations in tree order, `DRIVE_STATIONS`, `DELTA_VENDOR`; used by
  drive_param, sync_shift_sweep, bus_watch, and (2026-10-05) as the `--plc`
  default of the ten plc_direct tools.
- `xplc.py` (new, 2026-10-05): `xplc.Machine(transport)` with `UiRelay`
  (the UI's link, default) and `Direct` (own socket, plc_direct) transports;
  typed errors from the PLC's err text in one place (`Nak`, `Busy`,
  `BlockTimeout`, `NotReady`, `LinkDown`, `ReplyTimeout`, all RuntimeError);
  typed helpers (fsm_to, set_delta, drives_off, ec_stats, dwell, dem_stats,
  drive_sdo). `machine.py`'s link functions delegate to one
  `Machine(UiRelay())`; its other functions are unchanged. Next: move the
  plc_direct tools to `Machine(Direct())` one at a time (note `Plc.sys(cmd,
  timeout=)` vs `Machine.sys(cmd, timeout_ms=)`), then fold machine.py's
  FSM/delta helpers into Machine.
- `machine.drive_sdo` (2026-10-05): the one SDO read/write path with the
  Delta vendor check before the first write to a station; drive_param and
  sync_shift_sweep use it.
- `sync_shift_sweep.virtual()` raises when the delta did not go virtual.
- `joint_bench real` and `powered` (with a real delta) need `--owner-ok`.
- `soak_segments` uses `machine.require_owner_ok` (flag or `XPLC_OWNER_OK`).
- `circle_soak` fallback to `DELTA_MODE real:0` only when every delta axis
  is off (0) or in errorstop (1).

## 3. Verification

- Build (import_all into the open project): 0 errors, 98 warnings (the
  same warnings as before).
- **First download (16:44) failed to start**: the slot guard in 40c0773 had
  `(pSlot <> 0) AND (pStart >= pSlot^.buf)` in one condition; ST evaluates
  every operand, so the unbound packer in MsgPakInfoInit dereferenced NULL
  in the first cycle (`GVL.AxisGroupSMScans` = 1, the EtherCAT task gone
  from the Intewell `task` table). Nested IFs since 3e8e2b9.
  `machine.safe_install` had passed "download done" as success; it now
  needs "post-start state: run".
- Second download (16:5x, 3e8e2b9): runs, 7 slaves OP, `lost` 0. Checks
  with the delta virtual:
  - `EC_STATS tr:1 obj:1`: no reply (dropped whole),
    `ReMpOverflowDropCount` 0 -> 1, the next PING / TASK_STATS /
    DEM_STATS snap answered normally. Nothing corrupted.
  - DIRECT start while the FSM is Powered: state 99 `not_allowed`,
    `enabled` false at once, target / cw unchanged a second later.
  - GroupEnabling then failed with SMC 11000 (SMC_AXIS_GROUP_WRONG_STATE,
    SMC_GroupDisable reported the same): the group was left in a state that
    refuses Enable and Disable, most likely by the failed first download
    (asda_stale_target doc, section 6, saw 11000 in a similar situation).
    Cure as documented there: download again.
- Third download (17:04, same code): UnInited -> Powered -> GroupEnabled ->
  GA_EV 7 -> **Ready** with the virtual delta (skip-home on the drives' own
  virtual flag works), back to UnInited, axes 0/0/0.
- Not checked: GA_EV 7 with a real delta (needs the owner), REEL_RESUME
  after a manual reel move, DIRECT's abort with the drive enabled.
- The "PLC logger returns nothing newer than 2026-10-03 16:41" scare was
  the host's fault: `machine.save_plc_log` passed the job file as a path
  relative to codesys_scripts while `rpc` runs in the repo root, so the
  job never ran and the stale `plc_log_PlcLog.txt` came back every time
  (fixed 2026-10-05: absolute path, and the call fails unless the job
  reports `written:`; `read_plc_log.py` also died on the micro sign in
  SoftMotion messages, now ASCII-only output). The PLC logger itself is a
  normal 500-entry ring (CmpLog defaults).
- 2026-10-05: UnInited held `SMC_GroupReset` / `SMC_GroupDisable` /
  `fbResetReel` Execute TRUE every scan, which logged "GroupFBError 78:
  there is no error to reset" every 3 s (and 2AF8 when the group was
  already disabled), so the 500-entry ring held only minutes. They now
  execute only with something to do (group in errorstop / not yet
  disabled / reel in errorstop). Downloaded 01:42, FSM to Ready and back
  fine, one such entry in the following 8 minutes instead of ~160.

Machine left: program 3e8e2b9, FSM UnInited, delta virtual and off, 7
slaves OP.
