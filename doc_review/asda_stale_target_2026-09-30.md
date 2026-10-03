# ASDA stale target: delta vibration investigation (2026-09-30)

Status: **solved 2026-10-02** (section 7l). The cause: the slave order in
the project did not match the wire. EC0808DN is first in the configuration,
so CODESYS used it as the DC reference clock, but it was wired 6th, after
the three drives. With EC0808 wired first (matching the configuration) the
drives take the right target every cycle (0.00 % late), with EC0808's DC on
or off. Sections 1-7k are the history of the search, including the earlier
(wrong) conclusion that it was inside the ASDA-B3-E drive.

## 1. Symptom

The owner reported (long before this session) that the delta arms show slight
knocks or vibration over long runs, "as if the motor skipped a few position
frames". Reproduced today on the real delta with `tools/square_dip.py`:
- square ±50 mm, Z dips to −15, corner distance 14, 50 % of production dynamics;
- F 1000, ACC 100000, JERK 400000.

On the ASDA-Soft scope (EAxis0; Cmd Pos, Fdbk Pos, Following error, Motor
current, 8 kHz) the drive's command position, which it interpolates from the
bus target, sometimes:
1. repeats the previous 1 ms segment;
2. then jumps back against the motion in the first 1/8 ms sample;
3. then catches up at ~2x.

The following error and the current spike at the same instant. This is
the vibration the owner feels.

Example (scope17, per-sample increments of Cmd Pos, rows = 1 ms):

```
87562 ms: -11517 x8
87563 ms: -11517 x8        <- same segment again (stale target)
87564 ms:  67662, -24481 x7 <- jump back, then ~2x
87565 ms: -24481 x8
87566 ms: 169395, -26457 x7
```

Rate: 1-3 events/s while moving, in bursts of 2-50 events over 10-350 ms,
with quiet stretches of up to ~40 s.

## 2. Ruled out, with evidence

| Suspect | Test | Result |
|---|---|---|
| PLC trajectory (SoftMotion float) | d2 of `fSetPosition` every cycle, threshold 0.2 deg/cycle^2 | 0 glitches, max 0.032-0.037 |
| Integer target | d2 of `diSetPosition`, threshold 150000 inc | 0 glitches, max 45.7k-52.8k (= normal) |
| SoftMotion driver writing the PDO | Read-only `AT %QD12/16/20` taps on each drive's Target Position in the output image, compared with `diSetPosition` every cycle | 131,012 / 131,012 cycles equal to this cycle's value; 0 equal to last cycle's; 0 d2 glitches |
| Master / wire / NIC | `GetStatistics` lost / tx_err / rx_err | 0 / 0 / 0 over hours |
| Frame timing vs SYNC0 | EasyCAT (ESP32) at the **end of the wire**, DC-synced like the drives: PLC cycle counter checked at every SYNC0, frame arrival time measured | SyncOffset 50: 0 stale, 0 skipped in >1M cycles; frame arrives 660-825 us after SYNC0, >=175 us before the next |
| Same data on the bus | EAxis0's Target Position PDO copied into the EasyCAT outputs (same frame); ESP32 checks its d2 at SYNC0 | 0 glitches, max d2 45,728 (identical to the PLC side) |
| Frame split | Master `SplitFrame` FALSE, `FrameAtTaskStart` TRUE, `TaskSync` TRUE, 7 slaves on one port | one frame carries every slave's data |
| Task timing | `GetCurrentTimeInCycle` at the end of EtherCAT_Task (500 ms min/max ring) | IEC code ends 90-290 us into the cycle, locked, no drift |
| Planner load / path complexity | Two-point line (X ±50, exact stops) instead of the square | ASDA still 1.61 stale/s (scope17) |
| Drive sync config | SDO: 0x1C32:01 = 2 (DC SYNC0), :02 = cycle, 0x60C2 = 1 x 10^-3 s (all three drives) | correct; SM-event missed / cycle too small / sync error = 0 / 0 / 0, **also while the stale targets happen** |
| Cycle time | 2 ms bus cycle (drives follow: 60C2 = 2 ms) | ~0.1-0.2 % of segments stale, about the same fraction as at 1 ms (scope15/16) |
| Drive read delay P3.009 Z | EAxis0 0x5055 -> 0x5355 (read 300 us after SYNC0) | no change (scope7); set back to 0x5055 |

**Conclusion so far.** The drive's EtherCAT side receives every new target
in time. Its own sync counters stay at 0. Still, its control loop uses the
previous target for about 1 in 500-1000 segments. This is inside the
ASDA-B3-E (firmware `B3-E-Ver22106`), not the PLC, bus or wiring.

## 3. SyncOffset: what it does here

The master's SyncOffset (% of cycle) moves SYNC0 relative to the frame.
Measured at the EasyCAT (frame arrival after SYNC0, 1 ms cycle):

| SyncOffset | Arrival after SYNC0 | ASDA EAxis0 stale rate |
|---|---|---|
| 0 (original) | not measured | bursts every few ms (first photos) |
| 20 | < 420 us (the probe's floor) | 8.7/s (scope) |
| 50 | 660-825 us | 0.3-2.4/s (scope2-4, 9-14) |
| 75 | 910-1055 us (sometimes after the next SYNC0) | 1.8-2.8/s (scope5-7) |

50 is the best setting. 75 lands the frame at or past the next SYNC0.
Why 20 was worst although its frame arrives early is not understood. The
remaining stale targets at 50 are the drive-internal issue in section 2.
The setting is kept at **50** (`jobs/templates/set_ec_sync_offset.py`,
`WANT`).

## 4. Machine and PLC state at the end of the day

- PLC: 1 ms bus cycle, SyncOffset 50, build of commit 6bdea6a.
  `BusPeriod` / `EcNominalUs` are read from the EtherCAT_Task interval at
  the first scan.
- Delta: **virtual** (runtime mask 7), powered off (FSM UnInited). The arm
  was left at about X −50 Y −50 Z 0, held by the gearboxes (no brakes).
- **Modbus RTU branch disabled** (section 6b).
- **The group was enabled in virtual mode** (dry runs of pulse_test and the
  batch rounds). Before the real tests, run `rpc.py install --on-site` again
  (delta off), then `joint_bench.py real`, then power up and home. Otherwise
  GroupEnabling fails with 11000 (section 6).
- EAxis0 P3.009 = 0x5055 (the owner set it back).
- EasyCAT firmware: bus timing and position-stream probe (commit 6bdea6a).
- Bus order (auto-increment addresses), wire order:

  | # | Slave | Station |
  |---|---|---|
  | 1 | QEC | 1006 (DC reference) |
  | 2 | reel | 1002 |
  | 3 | EAxis0 | 1003 |
  | 4 | EAxis1 | 1004 |
  | 5 | EAxis2 | 1005 |
  | 6 | EC0808DN | 1001 |
  | 7 | EasyCAT | 1007 |

- **Unexplained mapping:** EAxis1's Target Position PDO (ASDA_B3_E_CoE_Drive_1)
  is mapped to an unused variable `biSetPos` (manual address %QD16).
  EAxis0 and EAxis2 have no mapping. It seems harmless (SoftMotion's value
  is what goes out), but it is probably a leftover. Ask the owner, then
  remove it.

## 5. Tools and diagnostics added today

**Reorganised 2026-10-01.**
- The tools share `tools/machine.py`, which covers:
  - the UI link, with a reconnect;
  - the FSM;
  - real / virtual through `set_delta`, which writes both masks;
  - `drives_off`, `safe_install`, `save_plc_log`, `bus_up`.
- Tools that move the real delta refuse to run without `--owner-ok` (or
  `XPLC_OWNER_OK=1`).
- Deploy with `python tools/safe_install.py`.
- New tools: `tools/sync_offset_sweep.py` (refuses > 50) and
  `tools/comm_profile.py`.
- Indexes: `tools/README.md` and `codesys_scripts/jobs/templates/README.md`,
  regenerated by `python tools/gen_script_index.py`.
- The commands below now take `--owner-ok` where they move the real delta.



| What | Where | Use |
|---|---|---|
| Square / line path, long runs | `tools/square_dip.py` | `--continuous --minutes N --cor 14 --f 1000 --acc 100000 --jerk 400000`; `--path line --cor 0` for two points. Streams through the UI (`plc_stream_start`), logs EC_STATS every 10 s. Laps are budgeted at ~1 s each, so a line run ends early. |
| Constant-pulse single-joint test | `tools/pulse_test.py` | See section 8. Dry-run on virtual axes OK (integer checks are 0 there: virtual axes produce no integer target). |
| ASDA scope analysis | `tools/asda_scope.py FILE... [--cycle-ms 1] [--thresh N] [--detail MS]` | Stale events, bursts, per-sample detail. The file format is in its docstring. |
| Joint bench | `tools/joint_bench.py real / virtual / powered / uninited / state` | `real` = delta real for this PLC run only; a download or restart makes it virtual again. |
| SyncOffset | `codesys_scripts/jobs/templates/set_ec_sync_offset.py` (`WANT`) | Device-config change, then `install --on-site`. |
| Bus cycle | `codesys_scripts/jobs/templates/set_bus_cycle.py` (`CYCLE_US`) | Task + master + every slave's SYNC0; the program adapts by itself. |
| Drive PDO addresses | `codesys_scripts/jobs/templates/dump_drive_target_addr.py` | Target Position: EAxis0 %QD12, EAxis1 %QD16, EAxis2 %QD20. |
| EasyCAT byte map | `codesys_scripts/jobs/templates/add_easycat.py`, `firmware/easycat_esp32/src/main.cpp` header | Outputs 8-11: PLC cycle counter. Outputs 12-15: EAxis0 target copy. Inputs 11-14: position probe. Inputs 22-31: stale / skip / arrival / late. |
| UI harness | `plc_send_many` (8 in flight), `plc_stream_start` / `plc_stream_status` / `plc_send_many_abort` (MotorTestPage) | The harness caps one instruction at 15 s, hence the background stream. |

**SYS `EC_STATS`** (reset:1 starts the counters over):
- `lost` / `tx_err` / `rx_err`: master frame statistics.
- Task timing: `cycles`, `late100`, `late500`, `pmin`, `pmax`, `h0..h5`,
  `dc_out`, `agsm_max`, `tic_max`, `tic0..9`, `bus_us`.
- Float target glitches: `g0..2`, `gms0..2`, `d2m0..2`.
- Integer target glitches: `dg0..2`, `dd0..2` (threshold `GVL.DiGlitchThresh`,
  150000; writable online).
- PDO taps: `tg0..2`, `td0..2`, `tnow0..2`, `tprev0..2`, `tnone0..2`.
- EasyCAT: `e_stale`, `e_skip`, `e_amin`, `e_amax`, `e_apeak`, `e_late`,
  `e_late_sum`, `e_pg`, `e_pd2`, `e_pd2peak`.
- `sm0..2` / `se0..2`: the drives' 0x1C32:0B / :20.
- `obj:1` adds `o0..o2`: 12 sync objects per drive.
- `tr:1` adds `trl` / `trh`: the 20 x 500 ms cycle-position ring.

The reply without `obj` / `tr` is kept short. With everything, the reply
overflowed and timed out.

**SYS `TASK_STATS`** gives per-task execution and jitter. Measured on the
square path:
- EtherCAT_Task: avg 168-182 us, max 330-360 us.
- SoftMotion_PlanningTask: avg 32 us, max ~1.9-3 ms (preempted).

## 6. Pitfalls met today

- **Download only with the delta powered off.** Every install today was
  preceded by `joint_bench uninited` and a check that the axis states are 0.
- **After a download the delta is virtual.** If the group is enabled in
  virtual mode (for example by the UI's init button) and the delta is then
  switched to real, GroupEnabling fails with **SMC error 11000**
  (`enable_fb`). Fix: download again, then `joint_bench real` before any
  enable.
- **GA_EV numbers.**
  - EV_POWER_ON 2, EV_GROUP_ENABLE 4, **EV_HOME_GO 6**,
    EV_HOME_GO_FORCE_SKIP 7, EV_RESET 8.
  - 7 is refused on a real delta, and the FSM just waits in GroupEnabled.
  - The visu codes in UpdateRuntimeAndInputEvent are different.
- **CODESYS daemon.** It retires after its 4 h budget: "daemon not
  reachable". Restart with `supervisor.py kill --yes`, then
  `supervisor.py start`.
- **ESP32 serial.** Opening COM3 with default pyserial settings resets the
  ESP32 (DTR/RTS), which drops the EasyCAT to INIT and breaks the bus. Open
  it with `s.dtr = False; s.rts = False` set before `open()`.
- **Scope files.** Saved as `.parscp` (7-zip) or `.parscp.scp` (bare). Parse
  from byte 1000 (section 5). One file was named `scope8..parscp`.
- **`joint_bench.py virtual` used to leave the delta real after the next
  FSM reset.** It sent SET_AXIS_SIM 7 but left `GVL.AxisSimConfigMask` at 0,
  and UnInited re-applies that mask. Fixed 2026-09-30: it now writes 7 too.
  Verified that the mask stays 7 across a reset. The dry runs were checked
  at mask 7 before they ran; the real arm did not move.
- **Real delta runs need the owner at the machine.** Ask before every run.
  Once today a run was started after the owner had left; it was aborted
  with `plc_send_many_abort` and the delta powered off.
- **0x1C32:0B on station 1002** (the reel drive) reads 16960. It is not a
  counter and not a delta drive; the delta drives are 1003-1005.

## 6b. Task review (2026-09-30 evening)

Task configuration:

| Task | Priority | Interval | Watchdog | Contents |
|---|---|---|---|---|
| EtherCAT_Task | 0 | 1 ms | 2 ms | AxisGroupSM, PRG_EcatEsp, PRG_EcatStepper, PRG_SimIo, PRG_EventLog, plus SoftMotion interpolation and IO |
| SoftMotion_PlanningTask | 5 | 1 ms | off | Group path planning |
| Comm | 20 | 1 ms | off | TCP_MSGPAK_Server, PRG_MbProbe, PRG_EcatEspHttp; the Modbus RTU bus cycle (async) |

The priority order is right.

**Comm task.** It sometimes took 3.5-4.8 ms. Its POUs never took more than
~0.5 ms. The rest, ~1.7 ms about once a second, was outside the POUs:
- The Modbus feeder link fails: SmartBowlFeeder `RESPONSE_CRC_FAIL`,
  0 slaves communicating, re-initialising each 1000 ms response timeout.
- **Modbus disabled** (`jobs/templates/set_modbus_enabled.py`, `ENABLE`) at idle:
  - time outside the POUs: max 1704 -> 203 us;
  - scans over 500 us outside the POUs: 32 -> 0;
  - Comm max: 1727 -> 625 us.
- Modbus disabled, on the virtual square: Comm max 768 us (was 3500-4800).
- Profile keys in EC_STATS: `cs0..2` / `cb0..2` / `co0..2` / `cscan` /
  `ctask` / `cout` / `cout500`.
- **Open:** fix the feeder link (baud 19200 / parity / stop bits / station /
  RS-485 wiring) before re-enabling it, if production needs the feeder over
  Modbus.

**Later clean-up (ask the owner first).**
- Move non-real-time work out of EtherCAT_Task: host packet parsing, event
  log, EasyCAT, stepper, SimIo, the diagnostics. Load today: avg 17 %,
  max 36 % of the cycle.
- Remove leftovers: PRG_MbProbe, PRG_EcatEspHttp, PRG_EcatStepper,
  PRG_SimIo if unused, and `biSetPos`.

## 7. Scope recordings (owner's PC)

Location: `C:\Users\PC\Desktop\新增資料夾 (2)\`, EAxis0, 8 kHz.

| File | Condition | Stale |
|---|---|---|
| scope | SyncOffset 20 | 104 / 6.5 s |
| scope2-4 | SyncOffset 50 (4 = 75) | 16-32 / 20 s |
| scope5-6 | 75 | 249 / 122 s, 139 / 89 s |
| scope7 | 75, P3.009 Z = 3 | 285 / 116 s |
| scope8 | 75 | 176 / 113 s |
| scope9-10 | 75, integer-target monitor 0 | 88, 173 / 120 s |
| scope11-13 | 50, EasyCAT clean | 198, 219, 9 |
| scope14 | 50, drive 1C32 counters 0 | 199 / 95 s |
| scope15-16 | 2 ms cycle | 59 / 78 s, 55 / 120 s |
| scope17 | 50, two-point line | 143 / 120 s |

Counts are from `tools/asda_scope.py` (1 ms: `--cycle-ms 1`; 2 ms runs:
`--cycle-ms 2`).

## 7b. New on-PLC check: the drive's own position demand (2026-09-30 night)

The owner's idea: the ASDA can send back the command it actually uses, so
the check needs no scope and lives in CODESYS.

**Mapping change.** 0x6062 Position demand value is PDO-mappable on the B3-E
(ESI: PdoMapping T), and the TxPDOs are not fixed.
- `jobs/templates/set_drive_demand_pdo.py` puts 0x6062 into TxPDO 0x1A01 (the
  one assigned to SM3) in place of 0x60BA, the touch probe position. Both are
  32 bit and 0x60BA is unused here.
- The job edits the entry's struct field (`prm[0].value`); assigning the whole
  struct is refused.
- `RESTORE = True` undoes it.
- Downloaded 2026-09-30 23:07: all slaves reached OP with the new mapping.
- The IEC channel keeps its name "Touch Probe Pos1 Pos Value".

**PLC side** (read-only AT taps, `GVL.st`):
- `DemandTap0/1/2 AT %ID13/19/25`: the drive's 0x6062.
- `ActualTap0/1/2 AT %ID10/16/22`: 0x6064, a live check. It read the arm's
  real joint angles (−1.86 / −32.6 / −43.3 deg) while the demand read 0 with
  the drives off.

**Comparison** (`PRG_EventLog`, EC_STATS with `dem:1`), per cycle, per drive:
- `dl{k}_{n}`: cycles where the demand equals the target we sent n = 0..3
  cycles before; `_4` is none of them. This shows the drive's normal delay.
- `ds{k}`: cycles where our target moved but the demand did not, i.e. stale.
  `dlt{k}` is the PLC ms of the last one.
- `dgl{k}` / `dmx{k}`: |d2(demand)| over `DiGlitchThresh` / its max.

**Open.**
- Is 0x6062 in CSP the received target, or an interpolated point? The lag
  histogram tells on the first real run.
- If it is interpolated or offset, adapt the comparison; `ds` (no motion of
  the demand while the target moves) should hold either way.

**Also tonight.**
- Following-error jump monitor (`fj`/`fm`/`fl` 0..3; 3 = reel,
  modulo-corrected).
- On the reel (vendor 0xA79, not Delta) at 400 mm/s, fe jumps went up to
  0.44-0.48 mm, about one cycle's travel.
- Inconclusive: fe jumps mix real stumbles with feedback latch jitter. That
  is why the demand readback matters.

## 7c. First real results of the demand readback (2026-09-30, 23:17-23:47, remote, owner OK)

**Normal delay.** The drive's demand 0x6062 equals the target we sent
**3 cycles** earlier (`GVL.DemLagNominal` 3).

**Stale events.** When a stale target happens, the demand goes one cycle
further back: lag 4. The increments then show a repeat or 0 step, followed
by a double step.

1/10 square at 30 %, SyncOffset 50, ~85 s:
- lag 4 ("late") on ~3 % of the cycles (1972 / 1375 / 1455 per axis);
- demand |d2| up to 253k-687k while the sent target stayed under 34k.

Snapshot, EAxis0, per-cycle increments:

```
sent:  41043 43404 44989 45792 45844 45662 45591
drive: 41043 43404 43404  1585 91636 45662 45662 -71
```

**Single joint, no planner** (`tools/pulse_test.py`, FSM Powered), SyncOffset 50:
- 0.1 deg/s, sent increments 144 / 145 (d2 ≤ 2):
  - lag 4 on 4.3 % of the moving cycles;
  - demand increments like `0 145 145 -1 145 144 289 289 0 ...`.
- 5 deg/s: lag 4 on 2.3 %; demand d2 21,671 vs 38 sent.

So the planner, path and kinematics are all ruled out. The drive's pick of the
new target flips between two frames.

**SyncOffset sweep.** Pulse test at 0.1 deg/s, one download each, no homing:

| SyncOffset | lag-4 (late) | demand d2 max | EasyCAT arrival after SYNC0 |
|---|---|---|---|
| 30 | 5.62 % | 434 | 549-700 us |
| 40 | 3.42 % | 434 | 556-719 us |
| 50 | **0 %** | 2 | 687-819 us |
| 60 | **EtherCAT did not start** | | |
| 70 | (bus already down) | | |

Notes on the sweep:
- SyncOffset 50 gave 4.3 % half an hour earlier (another download). The
  result may depend on the phase each EtherCAT start happens to lock at.
  **Repeat 50 across several restarts.**
- **SyncOffset >= 60 broke the bus.** The PLC log (the owner exported it
  from the IDE, 500 entries back to 23:44) shows what happened:
  - The SyncOffset 70 download (23:44:28) did reach "All slaves in
    operational" (23:44:33).
  - About 10 s later the axes read 0 and power-up failed.
  - Every download after that (23:46, 23:48, 23:52) and the cold reset /
    start left **no EtherCAT startup line at all**, not even "Networkadapter
    opened".
  - A bus scan at 0:11 got "SysEthernet: packet could not be sent, error
    code:20", then "no slaves found".
  - So the PLC's EtherCAT master / NIC layer (I210_2) wedged while running
    at 70. It is not the drives. Only a PLC restart re-initialises that
    layer; application downloads and resets do not reach it.
  - Why 70 wedges it is not in the log. At 70-75 the frame reaches the
    slaves at or after the next SYNC0 (EasyCAT: up to 1055 us), which
    probably upsets the DC task sync.
  - The 60 run is out of the log.
  - The recurring "vnet1 ... Error Code 39 / different subnet" lines are
    another interface and predate all this.
  - **Stay at 50.**
- **Reading the PLC log remotely** (2026-10-01):
  - Run `PYTHONIOENCODING=utf-8 rpc.py exec --readonly --file
    jobs/templates/read_plc_log.py` (device-level connection, no login). It
    writes `jobs/plc_log_PlcLog.txt`, oldest first.
  - The job uses the IDE's logger service (IOnlineDevice3.
    CreateLoggerServiceHandler) and pumps the IDE message loop while it waits.
    A plain End*() deadlocked the daemon.
  - The log keeps 500 entries: read it before retrying.

## 7d. 2026-10-01: phase per start, drive settings, filters

**Late % changes with each EtherCAT start.** After the PLC restart
(`stale_suite.py repeat`, SyncOffset 50, single joint 0.1 deg/s, three
downloads):
- 0.04 %, 3.13 %, 4.46 %;
- EasyCAT clean every time, frame arrival unchanged (662-825 us).

**Servo off/on does not re-pick the phase.** Four cycles in one start:
3.05 / 3.43 / 3.87 / 4.16 %. The rate rose slowly over ~5 min, so the drive's
phase drifts within a start.

**Production-like rounds do not avoid it.** `square_dip --batch 8`, same start:
- rounds: 3.3 / 3.8 / 5.7 % of the moving cycles;
- continuous: 3.8 / 3.6 / 3.5 %.

**Drive parameters over SDO.**
- `tools/drive_param.py read|write|restore`, through the PLC (SYS DRV_SDO /
  DRV_SDO_RESULT). Pg.nnn is object 0x2000 + g*0x100 + nnn.
- Writes go to EEPROM. Originals are kept in
  `codesys_scripts/jobs/drive_params_backup.json`.
- The manual (B3 user manual, CSP block diagram p.770) shows these filters in
  the CSP path: 2108h = P1.008, 2119h-211Ch = P1.025-028, 2124h = P1.036,
  2144h = P1.068.

The three drives were set differently:

| | EAxis0 | EAxis1 | EAxis2 |
|---|---|---|---|
| P1.068 moving filter | 2 ms | 10 ms | 4 ms |
| P1.008 low-pass | 10 ms | 20 ms | 20 ms |
| P2.000 position gain | 351 | 479 | 351 |
| P2.002 feed forward | 0 | 0 | 0 |

So feed forward is off: it is not what makes the knock.

- **P1.068 is now 6/6/6 ms** on all three (kept). In one start, 2/10/4 ->
  6/6/6 gave no clear change in the PLC following-error jumps; EAxis2's max
  jump went 0.204 -> 0.125 deg. That metric mixes in normal acceleration;
  torque 0x6077, already in the TxPDO, would measure the knock better.
- **P3.019.Z = 1 (statusword bit 14 = SYNC_OK) cannot be used with
  SoftMotion.** The SM3 Delta driver reads bit 14 as the positive hardware
  limit, so every axis went errorstop and would not power. Restored to
  0x21 on all three.
- P3.022 = 0xFF04: the drive tolerates 4 cycles without a new PDO before
  AL3E3. That is why a late target raises no alarm.
- The manual's AL304 advice ("computing time too long: disable USB
  monitoring") suggests ASDA-Soft over USB loads the drive CPU. The
  2026-10-01 runs were made with the USB unplugged and still showed 3-4 %.

**Open.** At 10:03:50 the PLC log shows an application download that these
tools did not make. Check whether someone downloaded from the IDE.

## 7e. 2026-10-01: direct drive test, SoftMotion bypassed

`tools/direct_test.py` (PLC: SYS `DIRECT`, end of `PRG_EventLog`). EAxis0
is made virtual in SoftMotion, so SoftMotion no longer writes its PDOs
(the target in %QD12 reads 0). PLC code then writes the controlword
(%QW22) and 0x607A (%QD12) itself: CiA402 enable, a constant +145 PUU per
1 ms cycle up 1.5 deg, then back. It stops itself on a drive fault, a
following error over 0.5 deg, or 2 s without the host heartbeat. The 0x6062
checks (DEM_STATS) run unchanged.

Results (owner OK, remote, 10:39-10:41):
- `--hold-only`: the drive enabled (sw 0x1637) and held. Our controlword
  reaches the drive.
- First move attempt: stopped after 1,987 cycles by the heartbeat guard (a
  host bug: the heartbeat ran in a second thread over the shared UI link;
  now sent from the poll loop). In those 1,987 cycles: 48 late (2.4 %).
- Full move, 29,892 cycles: lag histogram (0..6, none)
  `1106 1 24 28306 1563 0 0 0`. **1,563 late = 5.2 %**, 58 stale. Bin 0 is
  the enable / hold phases. Bin 2 (24) means the drive also sometimes
  takes the *newer* target.
- Snapshot (per-cycle increments, PUU):
  ```
  sent  (607A): 145 145 145 145 145 145 145 145 145 145 145 145 145 145 145
  drive (6062): 145 145 145 145 145   0 145 145 145 290 290 -145 145 145 145
  ```
  The lag goes 3 -> 4 (the 0) -> 3 -> 2 -> 3. The drive's pick-up of the
  target wanders around the latch point in both directions.

**Conclusion.** No SoftMotion, no planner, no axis group: the PLC writes a
constant ramp straight into the PDO, and the drive still uses targets from
the wrong cycle at the same or a higher rate. This is the cleanest case for
Delta (added to `delta_report_asda_b3_stale_csp_target.md`).

**Re-running it:** `asda_direct_test_runbook.md` (commands, scope settings,
analysis, baseline numbers).

**Machine back to the packing-machine configuration (2026-10-01 12:18):**
- P1.068 restored on all three drives (2 / 10 / 4 ms); P3.019 0x21;
  P3.009 0x5055.
- Delta virtual, powered off.
- Kept on purpose:
  - SyncOffset 50 (0 was clearly worse);
  - 0x6062 in TxPDO 0x1A01 (0x60BA was unused);
  - the diagnostics and SYS DIRECT (idle unless started).
- Modbus RTU still disabled at the owner's request.

## 7f. 2026-10-01 afternoon: burst timing across the three drives

**New PLC log.** `GVL.DemEvt` / SYS `DEM_EVT`: per drive, the PLC ms of
every start of a late or early stretch of 0x6062. `tools/dem_events.py`
groups the events into bursts (gap 300 ms) and lists, for every burst of
each drive, the nearest burst of the other two. Cleared by DEM_STATS
reset:1.

**Test.** Real delta at X0 Y0, Z 0 <-> -5 mm, F 5 mm/s, exact stops, 15
strokes, 60 s. A pure Z move turns the three joints alike, so the three
drives see the same motion.

**EAxis0 firmware.** The owner updated EAxis0's firmware (version still to
be noted). EAxis1 / EAxis2 keep B3-E-Ver22106. After the update:
- EAxis0 P2.000 was 364 (was 351);
- the drive rebooting on a live bus needs a re-download
  (`tools/safe_install.py`), otherwise SoftMotion's group enable fails
  with 11000 or the master reports a slave-count mismatch.

Results:

| Run | EAxis0 late | EAxis1 late | EAxis2 late | EAxis1 vs EAxis2 bursts within 500 ms (50 ms) | EAxis0 vs the others within 500 ms |
|---|---|---|---|---|---|
| EAxis0 new firmware, its own parameters (13:59) | 1,401 | 1,125 | 1,275 | 13 / 13 (8) | 1-2 / 13 |
| EAxis0 new firmware, factory parameters (14:10) | 1,054 | 1,444 | 1,410 | 12 / 14 (6) | 2-5 / 15 |

(An earlier 40 s run at 13:51, new firmware, own parameters: 6.2 % /
2.6 % / 2.4 % late.)

**Findings.**
- **The two drives on the old firmware burst together.** Most bursts are
  within 10-75 ms, with the same intervals (6.6, 5.6, 3.4, 2.43, 3.56, 2.7,
  2.40, 3.55, 13.6 s ...).
- **EAxis0 on the new firmware bursts at other times**, 0.4-3.4 s away, but
  its intervals come from the same family (about 2.4 / 2.6 / 3.5 s and
  their sums).
- Factory parameters (gains, filters, feed forward) did not change the
  picture. Drive tuning is not the cause.
- **Working hypothesis (not yet proven).** Two drives with their own clocks
  bursting together point at a common trigger: the frame's timing relative
  to SYNC0 has a slow periodic drift. The EasyCAT saw the arrival move
  within 660-825 us. Each firmware has its own pick-up point and fails
  when the drift reaches it. If so, the master side (CODESYS DC / send
  timing) drives the period and the drives are merely sensitive to it.
  The Delta report states the master as "clean"; that holds per cycle,
  not for timing.
- **Next test.** EasyCAT reports the frame arrival every cycle (now only
  min / max per window), logged with the burst times. If its peaks line
  up with the bursts, it is master timing. Then try the CODESYS DC options
  (DC sync mode, SyncOffset fine steps, task jitter) and give Delta the
  pick-up window question.

**EAxis0 factory reset, things to know.**
- Factory DI2-DI4 = 0x22 / 0x23 / 0x21: negative limit, positive limit,
  EMGS, normally closed. The DIs are unwired here, so all three are active
  and the drive cannot enable. EAxis1/2 have 0 (unused).
- Gear (P1.044/045/046), direction (P1.001) and P3.009/019/022 stayed the
  same after the reset.
- The owner restored the parameters with ASDA-Soft from
  `axis0Param2.par` (taken 14:02, after the firmware update). 21 key
  parameters were read back and all matched.
- **.par format** (ASDA-Soft V7.2.14). Records of 16 bytes: uint16 group,
  uint16 number, int32 value, 8 bytes unused. Groups 0-7 start at byte
  656 / 2240 / 4240 / 6272 / 6896 / 7616 / 9328 / 10928.
- `axis0Param.par` (09:52, before the firmware update) vs
  `axis0Param2.par`: P1.037, P1.061-063, P1.095, P2.000/002/004/006,
  P2.025/026/032/047/049/089/113/117/118 differ. Part of that is
  ASDA-Soft auto tuning, part the new firmware.

**State at 14:20.**
- Delta virtual, powered off.
- EAxis0: new firmware, parameters from `axis0Param2.par`.
- Bus: all OP. Modbus off.
- Left as is until Delta support responds.

## 7g. 2026-10-01 14:30: 10 ms EtherCAT cycle

`set_bus_cycle.py` with CYCLE_US 10000.
- The EtherCAT_Task interval had to be set as "10 ms": "10000 us" fails to
  build with "The task interval is invalid". The job now does this itself.
- All slaves reached OP. The drives showed 1C32:02 = 10,000,000 ns and
  0x60C2 = 1 x 10^-2 s.

Same Z test as 7f (X0 Y0, Z 0 <-> -5 mm, F 5, 15 strokes, 60 s, 3,600
moving cycles):
- lag histogram `2439 30 30 3600 0 0 0 0` on all three drives;
- **0 late, 0 stale, 0 burst events**.

At 1 ms the same test gave 13-15 bursts per drive per minute. So at a 10 ms
cycle the drives always take the right target, in line with a timing
window: 10 ms leaves the frame far from the drive's pick-up point.

Long run (15:14-15:19): continuous Z 0 <-> -5 mm stream, F 5, each G1
followed by G4 1 ms, no polling during the run (at 10 ms the PLC answers
host packets 10x slower, and polling DEM_STATS during a stream timed out).
- 313 s, 31,232 moving cycles on each drive.
- Histogram `163 2 4 31232 0 0 0 0` (EAxis2 `163 1 6 31231 ...`).
- **0 late, 0 stale, 0 burst events on all three drives.**
- At 1 ms the same 5 min would have given about 100 bursts per drive.

**The machine is left at 10 ms** until the owner decides. Return with
CYCLE_US 1000, then `tools/safe_install.py`.

## 7h. 2026-10-01 15:25: 5 ms EtherCAT cycle

`set_bus_cycle.py` CYCLE_US 5000. The drives showed 1C32:02 = 5,000,000 ns
and 0x60C2 = 5 x 10^-3 s. The test was the same continuous Z stream as the
10 ms long run (G1 + G4 1 ms, no polling): 318 s, about 63,000 moving
cycles per drive.

| Drive | Late | Stale | Burst events | Bursts |
|---|---|---|---|---|
| EAxis0 | 462 (0.73 %) | 5 | 16 | 11 |
| EAxis1 | 473 (0.75 %) | 18 | 27 | 8 |
| EAxis2 | 504 (0.80 %) | 18 | 33 | 9 |

- **Not clean, but far rarer in time.** There is a burst (or a pair 0.3-0.7 s
  apart) about every 30-60 s, against 2.4-3.6 s at 1 ms. At 10 ms there
  were none in 5 min.
- **All three drives show the same shrinking interval sequence:**
  - EAxis1: 62.2, 51.4, 43.9, 38.3, 33.4, 29.3 s;
  - EAxis2: 61.4, 51.3, 44.2, 38.3, 33.0, 29.6 s;
  - EAxis0: 60.5, 51.3, 43.7, 37.5, 33.5, 29.6 s.
- EAxis1 and EAxis2 burst together (7 of 8 within 500 ms). EAxis0 is on
  the same rhythm but shifted in time (0 of 11 within 500 ms).
- This fits a common, slowly varying phase drift that all drives see
  (master / DC side), crossing each drive's pick-up window. EAxis0's new
  firmware has a different window position.

## 7i. 2026-10-01 16:00-20:05: timing probes and DC settings (all at 1 ms)

All tests: X0 Y0, continuous Z 0 <-> -5 mm at F 5 (G1 + G4 1 ms), one EtherCAT
start each; late % = 0x6062 one cycle behind. The rate of one start varies
by 1-2 points on its own, and there are stretches of ~15 min where the
old-firmware drives run at 6-10 %.

New probes (EasyCAT firmware + PLC logs, `tools/dem_events.py`):
- Frame arrival by interrupt (INT carries SYNC0 + the output SM event):
  540-614 us after SYNC0, steady; no change at the drives' bursts.
- SYNC0 interval by MCPWM capture (12.5 ns): -13..+37 ns all the time,
  also at the bursts. The DC system time does not jump.
- (The LAN9252 0x09F0 timestamp is in the uncompensated local clock: it
  drifts ~7.6 ppm against SYNC0. Do not use it for phase.)

Tried, no effect on the rate or the 2.4 / 2.6 / 3.5 s burst rhythm:
- reference clock moved from QEC to the reel (QEC DC off, `set_qec_dc.py`);
- background SDO sweep off (`GVL.SdoSweepEnable`);
- drive DC sync0 shift time 100-400 and 100000-300000 (unit unverified,
  the drives do not implement 1C32:03);
- P3.009 Z = 2, 3, 4, 5 (three starts each for 3 and 5), Y = 10;
- master SyncOffset 0, 10, 20, 30, 40, 50 % (two starts each): means
  3.8-5.0 %, frame 43-104 us (0 %) to 542-614 us (50 %) after SYNC0.

One outlier: P3.009 Z = 8 gave 11 % on EAxis1/2 (one start, not repeated).

What does change it: the cycle (5 ms 0.7 %, 10 ms 0). EAxis1 / EAxis2 (same
firmware) burst within 50 ms of each other on most bursts; EAxis0 (newer
firmware) keeps the same rhythm at other moments. The cause looks
drive-internal and stateful; for Delta.

Later the same evening:
- QEC and reel DC off: EAxis0 becomes the reference clock (its own ESC
  makes its SYNC0). Still 4.0-5.0 % on all three, same rhythm, EAxis1/2
  together (20 of 29 within 50 ms). The reel's normal DC values are
  DC enable TRUE, sync0 enable TRUE, DCSetting 0 (QEC: 1 / 1 / 1).
- ASDA in Free Run (DC off, 1C32:01 = 0), still CSP (0x6061 = 8): the
  drives raise AL3E3 (PDO not received within P3.022's cycles) at once on
  enable, also with SoftMotion bypassed (direct_test.py). CSP on the B3-E
  needs DC; Free Run / SM sync is not an option.
  Second try: the drives have no OD 1006h (SDO error; 60C2 already says
  1 ms) and P3.022.YX at its maximum 0x14 (20 cycles) still gave AL3E3
  on enable. Restored DC and P3.022 0xFF04.

Settings restored: SyncOffset 50, shift 0, P3.009 0x5055, QEC DC on.

## 7j. 2026-10-01 night: cycle length, drive filter, settling

**Cycle** (`tools/dc_sweep.py cycle`, continuous Z test, late % mean):
1 ms ~3.8 %, 3 ms 1.04 %, 4 ms 0.92 %, 5 ms 0.76 %, 10 ms 0. But one stale
cycle is one cycle's worth of motion: at 5 ms it is 5x the step of 1 ms
(at F 1000 mm/s: 5 mm instead of 1 mm). The owner chose to stay at 1 ms
and soften the steps with the drive filter instead.

**Motion does not move the frame timing** (`tools/arrival_phases.py`,
standstill vs the square at 50 % / 80 %, 1 ms): frame arrival 542-603 us
after SYNC0 in every phase, 100 ms spread ~23 us in all.

**Feedback shock monitor** (PLC, SYS FB_STATS): per drive in Operation
enabled, histograms of |d2 actual position| and |torque change| per
cycle; torque taps `TorqueTap0/1/2 AT %IW24/36/48` (0x6077, 0.1 %).

**P1.068 sweep** (`tools/filter_sweep.py`, square with dips at 70 %, 60 s,
late % unchanged 3.6-4.5 %); torque change per cycle, max %, and count
of jumps >= 20 % per minute (EAxis0/1/2):

| P1.068 | max | >= 20 % per min |
|---|---|---|
| 2 / 10 / 4 (old) | 93 / 61 / 67 | 265 / 208 / 302 |
| 4 | 46 / 36 / 43 | 168 / 192 / 267 |
| 8 | 56 / 36 / 40 | 58 / 167 / 108 |
| 12 | 33 / 32 / 34 | 5 / 36 / 90 |

**Settling to 0.2 mm** (PLC SYS SETTLE: the actual TCP from
MC_GroupReadActualPosition after the set positions stop, 300 cycles;
`tools/settle_test.py`, PnP-like X +-50 with dips to -15 at 70 %, 31 stops):
old 1 / 2 / 3 ms (p50 / p90 / max), 4 ms 0 / 0 / 0, 8 ms 2 / 2 / 3,
12 ms 4 / 4 / 5. Error at the stop 0.14-0.45 mm.

**Decision (owner, 2026-10-02 00:14): P1.068 = 12 ms on all three drives**
(written by SDO, EEPROM). Mind:
- `codesys_scripts/jobs/drive_params_backup.json` still holds the old
  2 / 10 / 4; `tools/drive_param.py restore` would write them back.
- `axis0Param2.par` (owner's ASDA-Soft file) has P1.068 = 2; after loading
  it, set 12 again.

## 7k. 2026-10-02: QEC and reel wired out of the line

The owner wired the PLC straight to EAxis0 (QEC 1006 and reel 1002 out of
the line); `set_slaves_bypass.py` disables both slaves and makes their axes
virtual (EAXIS_A, SM_Drive_GenericDSP402 = the A rotation, reelpullmotor).
EtherCAT runs with 5 slaves; EAxis0 is the DC reference.

- SoftMotion cannot power the group this way: the FSM's Powering waits for
  SMC_GroupPower (the group includes the A axis) and for the reel's
  MC_Power, and axes under a disabled device are not processed even when
  virtual. The delta drives themselves enabled (sw 0x1637). A circle test
  would need the PLC to leave A and the reel out of the power-up.
- Direct PDO ramp (`direct_test.py`, EAxis0, +145 PUU per cycle, 1.5 deg):
  **late 4.93 %** (1,475 of ~29,900), stale 49 -- the same as with the full
  line (3-6 %). The other slaves on the line are not the cause.

Fixes made on the way:
- the axis-sim reinit only reinitialises the axes whose mode changes
  (AxisSimApplyFlip); the reel with a disabled slave failed ReinitDrive
  (81) and aborted a delta-only switch;
- `machine.set_delta` uses SYS DELTA_MODE (delta trio only, reel untouched).

To restore: wire back (PLC -> QEC -> reel -> EAxis0 ...), run
`set_slaves_bypass.py` with BYPASS = False, download.

## 7l. 2026-10-02: Delta's own PLC, then the cause: EC0808DN DC

**Delta's PLC.** A Delta engineer drove our ASDA-B3-E from their own PLC
(AX-C12, DIADesigner-AX, SM_Drive_ETC_Delta_ASDA_B3; 1 ms, SyncOffset 20 %,
sync window monitoring off; PLC wired to the drive alone). One axis up and
down: no skipping. Their scope files (`scope_otherPLC1/2`, 29.6 s + 8 s,
16 PUU/ms) have 0 bad cycles on a per-cycle-increment check that finds a
burst every ~2.5 s in our scope22. The owner also watched longer runs on
the scope: clean.

**Steps, all `tools/direct_test.py` (EAxis0, PDO written directly, 0.1 deg/s,
~30000 cycles):**

| Bus | Master | EC0808 DC | late % |
|---|---|---|---|
| full line (QEC, reel, 3 drives, EC0808, EasyCAT) | original | on | 4.93 (2026-10-01) |
| full line | like Delta: LRW, SyncWindowMonitoring 0, SyncOffset 20 | on | 5.05 |
| 3 drives only | like Delta | (unplugged) | **0.00** |
| 3 drives + EC0808 | like Delta | on | 3.71 |
| 3 drives + EC0808 | like Delta | **off** | **0.00** |
| full line, EC0808 last | original | off | QEC, reel stay SAFE-OP: AL 0x2D "No Sync error" |
| full line, QEC + reel + EC0808 DC off | original | off | 0.00, but the A axis does not move (set changes, actual stays) |
| **full line, EC0808 first on the wire** | original | **off** | **0.00**, all 7 OP |

Notes:
- The EasyCAT was not on the line when the vibration first appeared, so it
  is not the cause.
- With EC0808 off but last on the wire, QEC and reel (DC on) failed with
  "No Sync error" after a download, an application restart and a power
  cycle. With EC0808's DC back on they came up. Why the IO coupler's DC
  setting decides whether the first two slaves get SYNC0 is not known.
- The master settings like Delta's made no difference; they are back to the
  original values.

**Production configuration now:**
- Wire order: PLC -> EC0808DN (DC off) -> QEC -> reel -> EAxis0 -> EAxis1 ->
  EAxis2 -> EasyCAT.
- EC0808DN: DC enable 0, sync0 enable 0, DCSetting 0. QEC: 1 / 1 / 1. Reel:
  TRUE / TRUE / DCSetting 0.
- Master: SyncOffset 50, MasterUseLRW FALSE, SyncWindowMonitoring 1000.
- Checked on the machine: EAxis0 direct test 0.00 % (29892 cycles at lag
  3); `tools/a_axis_test.py`: all A positions as commanded;
  `tools/reel_real_test.py`: PASS (25 cells, 0-26 counts, drift 8.5).
- EC0808's IO without DC: outputs apply and inputs latch when the frame
  passes, not at SYNC0; the frame arrives 542-603 us after SYNC0 with ~23 us
  spread per 100 ms, still once per 1 ms cycle.

**Circle (shock) and PnP (settle), `tools/param_sweep.py`, 70 %, 60 s:**

| P1.068 | late % | torque change max % (EAxis0/1/2) | >= 20 % | >= 50 % | settle ms | error at stop |
|---|---|---|---|---|---|---|
| 0 (no filter) | 0 / 0 / 0 | 18 / 34 / 24 | 0 / 153 / 32 | 0 | 0 | ~24 um |
| 12 | 0 / 0 / 0 | 17 / 33 / 21 | 0 / 118 / 4 | 0 | 0 | ~186 um |

Before the fix, no filter gave ~700 % and 16 ms still 22-33 %. With the
cause gone the filter barely lowers the shock but adds lag, so **P1.068 = 0**
on all three drives (EEPROM, 2026-10-02 17:12; P1.008 0, P2.002 0,
P2.025 5). EAxis1 has the most >= 20 % changes; its gain P2.000 479 is
higher than the others' (~350).

**Still to do:** tell Delta the drive was not at fault; consider EAxis1's
gain.

**EasyCAT timing after the fix** (`tools/arrival_phases.py --speeds 1,1`,
round 3; the ESP32 had to be reset after the machine's power cycle -- its
LAN9252 lost the interrupt setup -- and the EasyCAT needed a re-download to
leave INIT):

| | before (2026-10-01) | after |
|---|---|---|
| frame arrival after SYNC0 | 542-603 us | 544-619 us |
| spread per 100 ms | ~23 us | p50 35, max 63 us |
| idle vs motion | same | same |
| SYNC0 interval - 1 ms | -13..+37 ns | -250..-200 ns, constant |
| drives late | 3.8-5 % | 0.00 % (6 motion phases) |

The SYNC0 period moved by ~225 ns per ms (~0.02 %): the DC system time now
follows a different reference clock. Likely reading (not yet verified in
the IDE's DC diagnostics): CODESYS took the first DC slave in the
configuration order as reference, which was EC0808DN although it sat 6th
on the wire. That would explain why moving the "reference" off QEC
(2026-10-01) changed nothing, why QEC and reel lost sync when EC0808's DC
went off while it was last on the wire, and the drives' periodic bursts.

**Confirmed: EC0808 DC on, wired first** (17:57): all 7 OP, SYNC0 interval
at the EasyCAT 0 ns (EC0808 is the reference again, as before the fix:
-13..+37 ns; with its DC off -225 ns), EAxis0 direct test **0.00 %**. So
EC0808's clock is fine as a reference; what broke the drives was the
reference sitting downstream of them because the configured order (EC0808
first) differed from the wire (EC0808 6th). EC0808DN and QEC are
"Optional" slaves in the project, which is probably why the master accepted
the mismatch (not verified).

Rule: keep the device tree order under the EtherCAT master the same as the
wire order; check after any rewiring.

**Enforced** (19:15, `jobs/templates/set_slave_order.py`, commit 17cb85c):
the tree is in wire order (EC0808DN, QEC, reel, EAxis0, EAxis1, EAxis2,
EasyCAT) and no slave is Optional any more, so the master addresses by
position and should refuse a wrong wire order; the station-alias check
stays (swapped identical drives). Before, the tree was EC0808DN, EAxis0,
EAxis1, QEC, EAxis2, reel, EasyCAT and matched no wiring -- only the first
slave (the reference) ever mattered. The reorder moved the drives' IEC
addresses; the GVL taps follow. Checked: all 7 OP, SYNC0 at the EasyCAT
0 ns, taps read sane values, EAxis0 direct test 0.00 % (drive enabled
through CtlTap0 at its new address).

**Station addresses moved with the reorder:** the master now assigns them
in tree order: EC0808DN 1001, QEC 1002, reel 1003, **EAxis0..2
1004..1006**, EasyCAT 1007 (read back by 0x1018). Tools and the PLC's SDO
sweep used 1003..1005; the first sweep after the download hit the reel
with a drive parameter read (failed, nothing written). Fixed (bdb23d3);
`drive_param.sdo()` now checks 0x1018:01 = Delta before any write. The
leftover biSetPos mapping on EAxis1's Target Position is gone (417b02f).

**Final check, this configuration** (`param_sweep.py`, 12 ms first, then
none):

| P1.068 | late % | torque change max % | >= 20 % | >= 50 % | settle | error at stop |
|---|---|---|---|---|---|---|
| 12 | 0 / 0 / 0 | 17.5 / 31.9 / 23.9 | 0 / 105 / 34 | 0 | 0 ms | ~188 um |
| 0 | 0 / 0 / 0 | 17.2 / 30.8 / 24.4 | 0 / 134 / 35 | 0 | 0 ms | ~24 um |

P1.068 stays 0 on all three drives.

**EAxis1 gain trial** (20:11): EAxis1's P2.000 / P2.004 / P2.006 (479 /
1916 / 305) are all ~31 % above EAxis0/2 (364-370 / 1457-1482 / 232-236),
the ratio of an auto-tune at a higher bandwidth. Lowered to 367 / 1470 /
234: EAxis1 torque change max 30.8 % (same), >= 20 % 142 (was 134), error
at stop 30 um (was 24). No gain; restored (21:40). The owner suspects a
stiffer reducer on EAxis1, which fits the higher auto-tuned gains; a
symmetric Z stroke comparing the three joints' torque would show it.

**1 h soak** (2026-10-03 07:47-08:47, `tools/circle_soak.py --speed 30
--minutes 60`, no drive filter): 3.6 M moving cycles per drive, late
0.00 % on all three, torque change per cycle max 5.7 / 7.5 / 8.5 %, none
>= 20 %, FSM Ready throughout.

Jobs: `jobs/templates/set_drives_only.py` (bus without QEC, reel, EC0808,
EasyCAT), `set_master_like_delta.py`, `dump_master_all.py`; EC0808 DC with
`set_qec_dc.py` (SLAVES = ("EC0808DN",)). `tools/machine.py`
`ethercat_state()` now logs out first ("Application not logged in" right
after an install or restart).

## 8. Next tests (before 7l; kept for the record)

00. **After the power cycle** (the bus is down since the SyncOffset-60
    download, 2026-09-30 23:41):
    - Check that EtherCAT is up: `xConfigFinished` TRUE, slaves OP,
      `ActualTap0` non-zero. The project is at SyncOffset 50.
    - Then `tools/stale_suite.py` (JSON lines with the demand figures):
      1. `repeat --n 3`: download plus a 1 min single-joint pulse test, three
         times. Is 0 % at SyncOffset 50 stable, or does it change with each
         EtherCAT start?
      2. `long --minutes 5`: single joint, long.
      3. `group --minutes 5`: home, then the 1/10 square at 30 % through the
         axis group.
    - The Intewell shell has no reboot, and its `ethercat` tool (IgH) prints
      nothing for the CODESYS master. Cold reset did not help.
0. **First look at the demand readback.**
   - Delta real, homed, then any slow motion (pulse_test).
   - Read EC_STATS with `dem:1`: which lag bin fills (the drive's delay), and
     whether `ds` counts.
   - With the ASDA scope recording at the same time, match `dlt` to the scope's
     stale events.
   - If they match, the scope is no longer needed.
1. **Constant-pulse single joint (first, with the owner at the machine
   recording EAxis0).**
   - Command: `python tools/joint_bench.py real`, home to Ready, then
     `python tools/pulse_test.py` (EAxis0 alone).
   - FSM Powered, group off: no planner, no kinematics.
   - Motion: up 3 deg at 0.1 deg/s (~144.5 inc per 1 ms cycle), then back
     down, ~70 s.
   - The PLC checks run with `DiGlitchThresh` 50 during the test.
   - Analyse with `python tools/asda_scope.py FILE --thresh 100 --detail MS`.
   - A stale target reads as a 1 ms segment of ~0 followed by ~289.
   - **Stale seen:** a clean minimal case for Delta.
   - **Clean:** raise the speed step by step (the `--vel` limit is 20 deg/s)
     to find where it starts; a speed or acceleration dependence would point
     at drive-internal filtering or feedforward.
2. **Production-like rounds** (owner's idea, 2026-09-30 evening).
   - Command: `python tools/square_dip.py --continuous --batch 8 --minutes 5
     --cor 14 --f 1000 --acc 100000 --jerk 400000`.
   - Each round is 8 G1s: the square-with-dips points in turn, blended
     inside the round, the last one an exact stop.
   - After each round the script waits for the motion to stop (~1-2 s at
     rest), then sends the next, like production (UI `MAX_IN_FLIGHT` 8,
     stops between placements).
   - Record EAxis0 and compare the stale rate with the continuous runs.
   - **Few or none:** it builds up in long continuous motion.
   - **Same rate:** it does not depend on how the motion is fed.
   - Dry-run on virtual axes OK: 26 rounds per minute, ~2.3 s per round.
3. **Other drives.** Record EAxis1 and EAxis2 the same way: is it all three,
   or one unit?
4. **ASDA-Soft load.** One run without ASDA-Soft connected, judging by feel
   and sound. Unlikely to matter: the vibration was felt before the scope
   was used.
5. **Mitigation while waiting for Delta.** The same position-command
   smoothing filter on all three drives (moving average 2-3 ms; check the
   B3-E manual for the parameter). Costs a few ms of lag.
6. **Report to Delta.** Contents:
   - sections 1-3 and 7, and the results of tests 1-2;
   - firmware B3-E-Ver22106;
   - CSP, DC SYNC0 1 ms and 2 ms, 0x1C32 / 0x60C2 values;
   - the EasyCAT control on the same frame.
7. **Clean-up after the investigation.**
   - Keep or remove the EasyCAT probes and the EC_STATS extras.
   - Remove the `biSetPos` mapping (ask first).
   - Fix the `square_dip.py` lap budget for the line path.
