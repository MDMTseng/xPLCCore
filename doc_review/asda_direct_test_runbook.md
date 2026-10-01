# ASDA-B3-E stale-target test: runbook

A repeatable minimal test of the ASDA-B3-E drives' CSP target pick-up, with
SoftMotion bypassed. Use it to:
- check a new drive firmware or parameter set from Delta;
- check a bus or SyncOffset change;
- reproduce the fault for Delta.

Background and history: `asda_stale_target_2026-09-30.md` (sections 7c-7e).
The report for Delta: `delta_report_asda_b3_stale_csp_target.md` (English),
`.zh-TW.md` (Chinese), `delta_report_asda_b3_summary.zh-TW.md` (summary).

## What the test does

`tools/direct_test.py` with the PLC's SYS `DIRECT` command (code at the end
of `PRG_EventLog.st`):
1. Powers the delta off (FSM UnInited).
2. Makes EAxis0 alone virtual in SoftMotion, so SoftMotion stops writing its
   PDOs.
3. The PLC writes EAxis0's controlword (%QW22) and 0x607A (%QD12) itself:
   - CiA402 enable: 0x06, 0x07, 0x0F, holding the actual position;
   - then a constant step per 1 ms cycle up `--deg`, then back, then hold.
4. Every cycle the PLC compares the drive's position demand 0x6062 (%ID13)
   with the targets it sent (DEM_STATS).
5. At the end it disables the drive and makes the delta virtual again.

EAxis1 and EAxis2 stay powered off. The arm moves by at most 3 deg on joint 0.

The PLC stops the test by itself (drive disabled, DirReason) on:
- a drive fault;
- a following error over 0.5 deg;
- 2 s without the host heartbeat;
- SoftMotion taking the axis back or the FSM leaving UnInited.

## Prerequisites

- **Owner at the machine and OK for this run** (the real delta moves). The
  tool refuses without `--owner-ok`.
- Delta powered off, arm clear of obstacles around joint 0.
- The deployed project still has:
  - SYS `DIRECT` and the DEM_STATS checks (commit 3d933a7 or later);
  - 0x6062 mapped into the drives' TxPDO 0x1A01 in place of 0x60BA. If it
    was restored to 0x60BA, apply
    `codesys_scripts/jobs/templates/set_drive_demand_pdo.py`
    (`RESTORE = False`), then `tools/safe_install.py`.
- Check: `python tools/drive_param.py read P3.009 P1.068` to record the drive
  settings of the run.

## Run

From `xPLCCore/`:

```
python tools/direct_test.py --owner-ok --hold-only          # enable and hold only (first check)
python tools/direct_test.py --owner-ok --deg 3              # +145 PUU/cycle, ~60 s
python tools/direct_test.py --owner-ok --deg 3 --step 72    # half speed, ~120 s (fits one scope recording)
```

| Option | Meaning | Default |
|---|---|---|
| `--deg` | travel, capped at 3 deg | 1.5 |
| `--step` | PUU per 1 ms cycle (145 = about 0.1 deg/s; 1,444,705 PUU per deg) | 145 |
| `--hold-only` | enable and hold, no motion | off |

Output, one JSON line plus snapshots:
- `hist`: how many cycles 0x6062 equalled the target sent 0..6 cycles back
  (last bin: none). Bin 3 is normal.
- `late` / `late_pct`: cycles at 4 back (one cycle late).
- Bin 2: one cycle early.
- `stale`: cycles where the demand did not move although it should have.
- Snapshots: 32 cycles around an event, sent vs drive increments.

Bin 0 and `d2max` include the enable and hold phases (the jump from the old
target to the actual position); ignore them.

## ASDA-Soft scope recording (optional, owner's PC)

- Drive: EAxis0 (station 1003), USB.
- Sampling 8K, 120 s.
- Channels:
  - CH1 Feedback position [PUU];
  - CH2 Command position [PUU];
  - CH5 Following error [PUU];
  - CH6 Motor current [%].
- Start the recording, then the `--step 72` run (120 s).
- Save as `.parscp`.

Analyse:

```
python tools/asda_scope.py FILE.parscp --ramp                     # bursts and intervals
python tools/asda_scope.py FILE.parscp --table 70838 70876        # per-cycle sub-steps + FE
```

A good cycle has 8 equal sub-steps (step / 8). A bad cycle shows:
- 2x or 3x sub-steps;
- a jump in the first sub-step.

## Baseline (2026-10-01, firmware B3-E-Ver22106, SyncOffset 50, 1 ms)

| Run | Cycles | Late (4 back) | Early (2 back) |
|---|---|---|---|
| 1.5 deg, step 145 | 29,892 | 1,563 (5.2 %) | 24 |
| 1.5 deg, step 145 | 29,892 | 1,719 (5.8 %) | 21 |
| 3 deg, step 145 | 59,782 | 1,997 (3.3 %) | 27 |
| 3 deg, step 72 | 120,394 | 4,366 (3.6 %) | 73 |

Scope `scope22.parscp` (step 72, 120 s):
- 44 bursts;
- 167 bad cycles (0.14 %);
- intervals 2.37-3.58 s (mean 2.76 s), repeating about 2.37 -> 2.58 -> 3.5 s.

`late_pct` counts stretches where the drive sits steadily at 4 cycles; those
move smoothly. The knocks are the switches between 3 and 4 (and 2), which
the scope's bad cycles count.

A fix should show:
- bin 3 holding all moving cycles;
- no bursts on the scope.

Repeat over several EtherCAT starts (`tools/safe_install.py` between runs),
since the rate varies per start (0.04-4.5 % seen).

## Burst timing across the drives

`python tools/dem_events.py` after any real-delta motion (DEM_STATS reset
first) compares the burst times of the three drives.

Reference test: X0 Y0, Z 0 <-> -5 mm, F 5, 15 strokes, 60 s. On
2026-10-01:
- the two drives on Ver22106 burst together (12-13 of 13-14 within 500 ms);
- EAxis0 on newer firmware bursts at other times.

See `asda_stale_target_2026-09-30.md` section 7f.

## After the test

- `direct_test.py` leaves the delta virtual and powered off.
- If the run was interrupted, the PLC stops by itself within 2 s (heartbeat).
- If needed, run `python -c "import machine as mc; mc.reconnect(); print(mc.sys_cmd('DIRECT', stop=1))"`
  from `tools/`.
