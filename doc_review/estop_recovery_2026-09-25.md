# E-stop, servo / bus faults: safety and resuming production (2026-09-25)

Branch `flow-analysis`. Question from the owner: what happens on an
emergency stop, a servo fault or an EtherCAT error, how safe is it, and
can production resume afterwards. Everything below was measured on the
local soft-PLC sim (all axes virtual) unless it says "machine".

## 1. What the software does today

- There is **no E-stop input** in the PLC or the UI. The IO map
  (`lib/production/io.ts`) has four reel sensors on DI 8..11, nothing for
  an E-stop or a safety relay. Whatever the E-stop does is hardware only;
  the software only sees its consequences (drive faults, bus errors).
- Faults the PLC catches (`AxisGroupSM` supervisor): EtherCAT master
  error, `GroupReadStatus.Error`, the group's ErrorStop,
  `GroupReadPosition.Error`, power / enable / homing FB errors, the UI
  heartbeat (5 s), COORD1 window exit. Each drives the FSM to **Error**.
- In Error: `MC_GroupStop` with the last G1's deceleration and jerk -- a
  controlled stop of the delta arm (not a safety stop). Motion packets are
  NAK'd `group_not_ready`. Recovery is EV_RESET -> UnInited (bus restart,
  power off) -> power -> enable -> **homing** -> coordinate system -> Ready.
- UI: a `group_not_ready` NAK on a queued move sets `current_error`; the
  cycle holds at the next checkpoint (the operator sees
  "input read failed: group_not_ready"). STOP ends the loop. RUN after
  re-initialising the motion (Operation page) starts a fresh run, which
  resumes the plan from the PLC's retained cell count (`PLAN_GET`).

## 2. Safety (needs the machine / electrical design)

Software stops are not safety functions. For the E-stop:

1. E-stop -> safety relay -> **STO on all four drives** (delta x3 and
   reel). Prefer stop category 1 (SS1: controlled decel, then STO) for
   the delta: with STO alone the arms fall under gravity unless the
   motors have brakes. Check the drives' STO / SS1 support and the brakes.
2. Decide what the nozzle does: a vacuum valve that drops out on E-stop
   (or on an EtherCAT drop, where the IO module goes to its safe state)
   drops the part wherever the arm is -- possibly into the tape or the
   mechanism. Holding vacuum is usually preferable.
3. Wire the safety relay's status (E-stop pressed / released) to a spare
   DI, so the PLC can report "E-stop" instead of a downstream drive fault
   and refuse the reset while it is pressed. Resetting stays a deliberate
   operator action; nothing restarts on its own.

## 3. Resuming production: what was wrong, what changed

Tested with fault injection (`tools/sim/run_virtual.py --fault N`):
the PLC is driven to Error at random moments, recovered the way an
operator would (reset, power, enable, ready), and RUN again. Checked with
`plan_check.py` (tape layout vs plan) and a reel-position audit.

Findings:

| # | Finding | Fix |
|---|---------|-----|
| 1 | Faults outside a tape move: plan resumed correctly (3 faults, PASS). | -- |
| 2 | A fault during a tape move: the reel's move FBs were only called while the group was Ready. SoftMotion computes set points inside the FB call, so the reel **froze mid-move** -- on a servo an instant stop from speed. The cells were not counted; the next step moved a full cell from the frozen spot, leaving the tape off its cell boundary (0.05-0.1 mm per fault on the sim; on an E-stop anywhere up to a whole cell). | The reel FBs are called every scan, ahead of the Ready gate: a fault on the arm lets the tape move finish on its boundary. |
| 3 | The PLC counted a tape move by the command's outcome, not by the tape. | A reel-move tracker counts the cells as the reel passes them (unwrapped travel / pitch; the reel is a modulo-200 axis), whatever the FSM does. A move that stops short stays open, retained (`GVL.ReelMv*`). |
| 4 | Nothing brought a stopped-short tape back onto a cell boundary. | TAPE_CYCLE NAKs `reel_interrupted` while a move is open. SYS `REEL_RESUME` moves the rest (cells counted as they pass); RUN does it first (`lib/production/recovery.ts`). If the PLC restarted meanwhile (`reel_pos_lost`) it stops and asks the operator; SYS `REEL_CLEAR {count}` closes the move after aligning by hand. |
| 5 | A part left on the nozzle by a fault or STOP was carried into the start moves and the bottom-camera check. | RUN first drops whatever the nozzle holds into the feeder bowl (`emptyNozzle`): re-inspected, not scrapped. |
| 6 | `plan_check.py` counted every reel move as at least one cell. | It counts the cell boundaries crossed, so a stopped-short move plus its REEL_RESUME is one cell. |

Sim results (plan 1,-10,60,-11,1, 0.5 % NG):

| Run | Faults | Tape layout | PLC vs renderer | Reel audit |
|-----|--------|-------------|-----------------|------------|
| fault1 | 3, anywhere | PASS | 83 = 83 | -- |
| fault4 | 3 arm faults mid tape move | PASS, reel finished its move (no resume needed) | 83 = 83 | 0.000 mm |
| estop1 | 3 E-stops mid tape move (reel stopped 0.05 mm in) | PASS, REEL_RESUME 7.6 mm each time | 83 = 83 | 0.000 mm |
| base5 | none (regression) | PASS, ~61 s as before | 83 = 83 | -- |
| chaosf5 | 4 STOPs + renderer plan forgotten | PASS | 83 = 83 | -- |

`GVL.TestFaultMidReel` (1 arm fault, 2 E-stop) and `GVL.TestReelFrozen`
are the sim's fault-injection hooks; 0 / FALSE in production.

## 4. Still open

- E-stop DI and a clear "E-stop" state (section 2.3); until then the
  operator sees `group_not_ready`.
- One recovery action in the UI (reset -> power -> enable -> home ->
  coordinate system -> RUN) instead of the Operation page plus RUN, and
  a readable cause (`LastErrorSource`, axis error mask).
- A UI for `reel_pos_lost` (jog the reel to the hole, then REEL_CLEAR).
- Machine: does homing after every fault cost much? Absolute encoders
  would allow skipping it. What does an EtherCAT drop do to the reel's
  position (the tracker marks a jump > 5 mm/scan as position lost)?
- A part placed in the pocket just before the fault is found by the next
  top-camera shot (tape NG path); a part the vacuum dropped elsewhere is
  not -- the operator has to look.
- Real-PLC install: `create_plan_count_cells.py` and
  `create_update_di_watch.py` (logged out), then the
  on-site install (retained variables added).

## 5. TODO: E-stop and PLC-failure stop (deferred by the owner, 2026-09-25)

Nothing is wired yet. Agreed direction, to pick up later:

- E-stop as SS1-t: dual-channel E-stop buttons -> safety relay with
  instant and delayed outputs. Instant: DI to the PLC (stop the arm and
  the reel with dedicated E-stop dynamics, refuse reset while pressed).
  Delayed (~1 s, above the worst-case stop time): STO1/STO2 of the three
  ASDA-B3-E drives, and a pair of safety contactors on the power of the
  reel (CL3-E57H) and A-axis (QEC) drives, if those have no STO. Cut
  torque, not control power: encoders keep the position for recovery.
- PLC failure must remove torque, in two layers: (1) drive fault
  reaction on EtherCAT loss = ramp down then disable (ASDA 0x605E /
  0x6085; keep the EtherCAT watchdogs on -- the sim has them off);
  (2) a heartbeat DO toggled by the main logic task -> retriggerable
  watchdog relay -> the safety relay's stop input (or in series with
  STO), which also covers "task frozen, bus still running". No
  automatic restart: manual reset.
- To confirm on the machine: STO on CL3-E57H / QEC, separate logic and
  power supplies, motor brakes on the delta, free DI for the E-stop
  state, free DO for the heartbeat, the IO module's safe state (vacuum).
