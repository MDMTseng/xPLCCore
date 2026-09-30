# ASDA stale target: delta vibration investigation (2026-09-30)

Status: **open**. The evidence points inside the ASDA-B3-E drive. The next
step is a single-joint constant-pulse test with the ASDA scope (section 8),
then a report to Delta. This document is the handover: what was measured,
what is ruled out, the tools, the machine state and the next tests.

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
- **Real delta runs need the owner at the machine.** Ask before every run.
  Once today a run was started after the owner had left; it was aborted
  with `plc_send_many_abort` and the delta powered off.
- **0x1C32:0B on station 1002** (the reel drive) reads 16960. It is not a
  counter and not a delta drive; the delta drives are 1003-1005.

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

## 8. Next tests

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
2. **Other drives.** Record EAxis1 and EAxis2 the same way: is it all three,
   or one unit?
3. **ASDA-Soft load.** One run without ASDA-Soft connected, judging by feel
   and sound. Unlikely to matter: the vibration was felt before the scope
   was used.
4. **Mitigation while waiting for Delta.** The same position-command
   smoothing filter on all three drives (moving average 2-3 ms; check the
   B3-E manual for the parameter). Costs a few ms of lag.
5. **Report to Delta.** Contents:
   - sections 1-3 and 7, and the result of test 1;
   - firmware B3-E-Ver22106;
   - CSP, DC SYNC0 1 ms and 2 ms, 0x1C32 / 0x60C2 values;
   - the EasyCAT control on the same frame.
6. **Clean-up after the investigation.**
   - Keep or remove the EasyCAT probes and the EC_STATS extras.
   - Remove the `biSetPos` mapping (ask first).
   - Fix the `square_dip.py` lap budget for the line path.
