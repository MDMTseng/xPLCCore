# Packing flow review (2026-09-26)

Three parallel reviews of the whole packing flow -- renderer flow modules
(`lib/production/`), page orchestration (`components/CalibPage.tsx`,
`PluginHello.tsx`), PLC command handling (`codesys_code/Application`) --
with the key claims re-checked against the code. Branch `flow-analysis`.
Status column is kept up to date as items are fixed.

## High: wedges production, or wrong books / wrong packing

| # | Finding | Where | Status |
|---|---------|-------|--------|
| 1 | Reel tracker "stopped" test is `|step| <= 1e-6 mm` for 50 scans: a real servo holding position jitters by ~1 count, so the move never closes; every TAPE_CYCLE / ReelGo / REEL_RESUME then NAKs `tape_busy` until a PLC restart. The sim's virtual axis has no jitter. | `AxisGroupSM.st` reel tracker | Fixed: "still" = within 0.02 mm of where the reel settled for 50 ms, plus a 20 s ceiling. Sim with +-0.005 mm jitter on the tracked position (`GVL.TestReelJitterMm`, `--reel-jitter`): production and E-stop recovery pass. |
| 2 | Error entry drops the reel's power at once: on a real drive an arm fault during a tape move leaves the tape between cells (the sim's "reel finishes its move" is virtual-axis behaviour). | `Update.st` Error state | Fixed: in Error the reel stays powered while a tape move is under way (`GVL.ReelMoveInFlight`); its own fault / an E-stop stops it anyway. Needs confirming on the machine. |
| 3 | REEL_RESUME computes the rest from the recorded travel only; a position change after the tracker closed (EtherCAT drop, manual ReelGo) goes unnoticed and the tape is over/under-advanced. | `DrainHostPackets.st` REEL_RESUME | Fixed: the tracker records where it closed the move (`GVL.ReelMvStopPos`); REEL_RESUME refuses (`reel_position_lost`) when the reel has moved more than 0.2 mm since. |
| 4 | Plan and open-move record are `RETAIN`: a download (the on-site install) re-initialises them. | `GVL.st` | Mitigated: a renderer that is still up restores the plan after a download (PLAN_SET with its own cell count). The open-move record is lost: the install checklist now says to install only with no open tape move (PLAN_GET `reel_open` false). PERSISTENT needs the Intewell target's support -- to test on the machine. |
| 5 | The plan can be replaced mid-run (editor Apply, recent entries, harness `set_plan`): books diverge, `applyAdvance` can throw after the reel moved. | `CalibPage.tsx` | Fixed: `setProductionPlan` refuses while a run is on (editor, recent entries, harness). |
| 6 | A throw in run set-up (start moves, emptyNozzle, BtmCheckCalib) leaves `isRunning` true for good: RUN silently refused until reload. | `CalibPage.tsx` runAllObjects | Fixed: `runAllObjects` wraps the run in try/finally; a set-up failure is shown and RUN works again. |
| 7 | RUN pressed while a STOP is completing clears the stop flag: the old loop runs on with the input watchdog already gone. | `CalibPage.tsx` RUN handler | Fixed: RUN is ignored while a run is on; the input watchdog ends with the cycle, not with STOP. |
| 8 | Endless loops with no limit or alarm: empty feeder refills, every part tossed (hole not found, correction out of range), an NG in the tape that is never picked out. | `cycle.ts`, `judge.ts` | Fixed: `FEEDER.MAX_EMPTY_REFILLS` 5, `INSPECTION.MAX_TOSSES_IN_A_ROW` 15, `NOZZLE.MAX_NG_PICKS_IN_A_ROW` 4 stop the run with the reason; offline tests for each. |
| 9 | STOP cannot end a run parked in an error hold. | `CalibPage.tsx` checkpoint handler | Fixed: STOP with an error hold on ends the run (next RUN empties the nozzle and re-syncs the plan from the PLC). |

## Medium

| # | Finding | Where | Status |
|---|---------|-------|--------|
| 10 | A hold longer than the top-shot TTL (3 s) with the arm over the tape: the PLC drops the shots (TRIGGER_ERR, not handled in production), the run later dies on a misleading vision timeout with a part on the nozzle. | `cycle.ts`, `tape.ts` | Fixed: top-shot TTL 60 s (`TAPE.TOP_SHOT_TTL_MS`, x speed override); vision waits do not count time held at a checkpoint; a TRIGGER_ERR for the shots fails the step at once with a clear message. |
| 11 | Error/UnInited flushes fly events silently: a pending WaitForTriggerMotionProgress is never answered, outputs can stay mid-sequence; M4s from a dropped session survive into the next one. | `UpdateRuntimeAndInputEvent.st` | Fixed: `FlushFlyEvents` on Error / reset / host disconnect NAKs pending waits (`group_not_ready`), fast-forwards pin sequences under way (single-stage outputs such as the vacuum untouched), reports unfired pin ops as TRIGGER_ERR 101, writes the outputs; disconnect also clears the duplicate filter. |
| 12 | Any vision-link status change rejects pending vision waits with a misleading message; vision requests have no timeout. | `PluginHello.tsx`, `CalibPage.tsx` || Fixed: the vision send no longer changes with the link state (status via a ref; the hook's `send` is stable), so a link change no longer rejects the run's waits as "left over promise"; vision requests time out (`VISION.REPLY_TIMEOUT_MS`) and fail at once when not sent; a dropped link rejects the pending requests and the camera waits with "vision link lost"; the link reconnects every 2 s by itself. |
| 13 | Every plan-sync failure is swallowed as "older PLC"; plan mismatches are console only. | `CalibPage.tsx` | Fixed: only an unknown-command NAK (older PLC program) falls back; other plan-sync / tape-check failures stop the RUN. Plan-book differences at the end of a run are shown to the operator. |
| 14 | Manual debug buttons stay live during a run (steal vision replies, move the reel). | `CalibPage.tsx` | Fixed: the manual/debug section ignores clicks while a run is on. |
| 15 | A NAK'd pack tape step surfaces only after pick and inspection (part on nozzle); the renderer plan is decremented before the PLC acks. | `cycle.ts` | Fixed: a failed tape step (pack or empty) is raised before the next pick; a plan ending in an empty segment waits for its last step. (Renderer plan still decremented before the ack: the PLC's count is the truth at the next RUN.) |
| 16 | Feeder Modbus writes are not awaited (failures unseen); a refill in flight is not settled when the loop ends. | `feeder.ts`, `PluginHello.tsx` || Fixed: feeder bridge writes stay unawaited (host timings) but a failure is kept and fails the next feeder step ("feeder bridge write failed"); a refill still in flight is settled before the page's clean-up. |
| 17 | An NG part can be re-judged into the feeder bin; emptyNozzle returns tape NG parts to the feeder. | `judge.ts`, `recovery.ts` || Fixed: a part that failed its own inspection (rectified measure, side / bottom check) stays part_ng whatever else goes wrong; `emptyNozzle` drops into the part-NG bin (one part scrapped per interruption, D.11), never back into the feeder. |
| 18 | PLC-side timeouts (tape 6 s, shot TTL 3 s) do not scale with the speed override. | PLC, `tape.ts` | Partly: the top-shot TTL and the TAPE_CYCLE timeouts scale with the override (tape.ts); PLC defaults apply only when the host sends none. |

## Low

- `placedUncounted` never decremented on an NG pick-out (efficiency only).
- One extra pick-and-toss when a pack step finishes a segment.
- Hard-coded values that belong in `params.ts`; dead code; mixed pack counts on screen.
- Tests missing: TAPE_CYCLE NAK, plan ending in an empty segment, STOP/hold at each checkpoint, feeder failures.
- Unverified (real-PLC behaviour): WaitForMotionStop acking before a move starts; negative parse index after a TCP reset.

## Batch 1 verification (sim, 2026-09-26)

`run_virtual.py --ui-guards` (RUN with the FSM in Error, plan change
refused mid-run, RUN during a pending STOP, STOP during an error hold,
then the plan to its end): all OK, tape layout PASS, 83 = 83 cells.
Reel jitter +-0.005 mm: normal run and 3 E-stops mid tape move PASS.
Offline: 91 unit tests.

## Batch 2 verification (sim, 2026-09-26)

Items 10, 11, 13, 14, 15 (and 18 in part). `--hold-test 3 --hold-s 12`:
three 12 s step-mode holds mid-run (past the 10 s vision budget and the
old 3 s shot TTL), no error, plan PASS. Faults anywhere, E-stops mid tape
move with reel jitter, chaos STOPs with the renderer plan forgotten, the
UI guards and a normal run: all PASS, 83 = 83 cells. A PLC fault now
reads "PLC left Ready" instead of "input read failed". 95 unit tests.

## Batch 3 verification (sim, 2026-09-26)

Items 12, 16, 17. `--vision-drop-at 15`: the link cut mid-run ends the
run within 3 s on "vision link lost (SideCheckData)"; after reconnecting,
RUN finishes the plan (PASS, 83 = 83). Regression: normal run, faults,
E-stops with reel jitter, chaos + forget, UI guards, 12 s holds, sensor
glitch: all PASS, 83 = 83. 97 unit tests (feeder bridge failure and the
part-NG verdict covered offline).

## Installed on the machine (2026-09-29)

After the owner's power cycle, the flow-analysis build (through the jog
streamer, 27013e6) was installed on the real PLC: Comm task 1 ms, the
three new methods created, build 0 errors, `install --on-site` (download
19.4 s, app back in RUN). Checked: scans ticking, FSM UnInited, no error,
EtherCAT master OK, test hooks off, EAxis0/1/2 still virtual. The
download re-initialised the retained plan (BootEpochCount 1); the
renderer re-sends it at the next RUN. Not yet exercised on the machine:
everything marked "needs the machine" above.

## Reel on the machine (2026-09-29)

`reelpullmotor` made real in the project (the delta arms stay virtual),
installed, and driven with `tools/reel_real_test.py` (direct msgpack
client `tools/plc_direct.py`, no UI; nothing on the reel):

- Standstill: 0 counts of jitter (closed-loop stepper). Item 1's band
  is not what matters on this drive; the settle below is.
- The drive settles a few counts short of the target (13 counts =
  0.05 mm after a slow move). The tracker required the travel within
  0.05 mm, so the first TAPE_CYCLE came back `reel_interrupted`. Fixed:
  - a cell counts within 0.5 mm of its boundary (REEL_CELL_TOL_MM);
  - a move is complete when the move FB reports Done.
  Only a move without Done (a fault) is judged by its travel.
- Production dynamics: 1 / 2 / 5 / 3 cells in 0.11-0.19 s, counts right.
  After fast moves the reel settles 0-26 counts (0-0.1 mm) off the
  target. Over 25 single cells this does not add up (drift -8 counts):
  each move starts from the commanded position.
- Item 2 confirmed: an arm fault (GVL.TestFaultMidReel = 1) during a
  2-cell move put the FSM in Error, the reel stayed powered and finished,
  cells_done +2, no open move.

## EtherCAT timing (machine, 2026-09-29)

The owner saw the odd DC-statistics timeout and suspected the arm's
computation. New EtherCAT_Task timing monitor (GVL.EcProf*, LTIME()
stamps in AxisGroupSM / PRG_EcatEsp / PRG_EventLog, DC-out cycles from
the master's xDistributedClockInSync) and `tools/ec_stress.py`: 20 s
phases idle / ready / arm (a blended G1 stream keeping the motion
buffer full: the delta kinematics and planning run on the virtual arms)
/ + reel moves every 250 ms / + back-to-back TCP queries.

- Under load: task period 955-1045 us, no cycle more than 100 us late,
  AxisGroupSM at most ~155 us per 1 ms (avg 47-51 us), DC never out of
  sync. The arm's load does not reach the EtherCAT task (priority 0;
  the planning task is priority 5).
- The DC loss came from every reset. UnInited entry restarted the whole
  EtherCAT bus (xRestart), even with the bus healthy. Each reset put the
  DC out of sync for ~3.4 s (3417 cycles, one 1190 us cycle) and
  re-initialised every slave. Fixed: the bus restarts only when the
  master reports an error or the last fault was the master
  (GVL.BusRestartOnReset restores the old behaviour). After the fix:
  3 resets, 0 DC-out cycles, cycles <= 1031 us. Full stress run again
  clean.
- Not measured from inside: the SoftMotion bus processing itself and
  the planning task's own time (only their effect on the period).

### Task statistics under load (machine, 2026-09-29)

SYS TASK_STATS returns the runtime's task statistics (what the IDE's Task
Configuration > Monitor shows), for all tasks. It needs the CmpIecTask
library: `jobs/templates/add_lib_cmpiectask.py`, run logged out before
the import.

30 rounds x 20 s of full load (`tools/ec_stress.py --rounds 30`), in
total 43k blended G1s, 2.7k reel moves and 93k queries:

- EtherCAT_Task: avg 208 us, worst 372 us, jitter +-34 us. Never late,
  DC always in sync.
- SoftMotion_PlanningTask: avg 241 us, but ~17 ms at least once in
  every round (its interval is 1 ms).
- Comm: delayed up to 19 ms behind the planning task (priority 20 vs 5).

The planning spikes follow the rate of blended short segments, not the
daemon or the reel:

| arm motion | G1/s | worst planning cycle | >5 ms windows |
|---|---|---|---|
| dense blended stream (stress) | 61 | 9 ms typical, 17 ms | ~10/s |
| dense, Cor 0 | 12 | 0.8 ms | 0 |
| blended, one G1 per 50 ms | 16.5 | 3.3 ms | 0 |
| long slow moves | 3.6 | 0.3 ms | 0 |

The EtherCAT task (priority 0) is not affected. The cost lands on the
planning task and, behind it, on the TCP latency. Open: how far the
planner can fall behind before the motion suffers, the planning task's
watchdog setting, and the production G1 rate.

Production-like replay on the machine: 42 parts of the production G1
sequence (pick 3, inspect 6, place 3 + lift; feed 2000 dynamics; Cor 45
sticky, so every G1 blends) at 0.54 s/part, about 24 G1/s. Planning task:
avg ~0.1 ms, worst cycle per 1 s window median 4.9 ms, worst 6.2 ms.
EtherCAT max 312 us, Comm max 2.0 ms. Each burst queues tens to hundreds
of ms of motion, far more than the planning delay, so the planner does
not slow the arm. The 17 ms spikes need the synthetic 60/s stream of
short blended segments. The PLC is sized with room to spare for this
work at 1 ms. Still to check: the planning task's watchdog (> ~10 ms or
off).

Found on the way: the PLC blends only from Cor >= 1 (BlendingNext);
below 1 a G1 is Buffered. JOG.COR 0.5 meant a stop between jog steps;
it is now 1.

Real production flow on the machine (`run_virtual.py --plc 192.168.1.70
--task-stats`: the UI's cycle and the vision mock against the real PLC,
virtual delta, real reel, simulated tape sensors switched on for the run
and off after). Plan 1,-10,60,-11,1: 83 = 83 cells, about 62 s.
Longest cycle per sampling window:

| task | median | p90 | worst | avg |
|---|---|---|---|---|
| EtherCAT_Task | 292 us | 307 us | 334 us | 174 us |
| SoftMotion_PlanningTask | 2.8 ms | 3.0 ms | 3.3 ms | 65 us |
| Comm | 1.9 ms | 3.0 ms | 4.7 ms | 59 us |

`plan_check.py` shows FAIL on this run. It reads the reel's position
from the event log at the "move end" event. The real reel is still
~1.6 mm behind the setpoint then (0.8 cell per move); the virtual reel
is not. The PLC count waits for the reel to stand still and is right.
To fix in the tool: take the settled position.

## The A axis on the machine (2026-09-29)

The rotation motor is an open-loop stepper on the second axis of the
QEC 3-axis stepper driver (M2 / "Y"). SpiderR's tool kinematics axis
`SM_Drive_GenericDSP402` (drive ID 7, logical device 1) maps to exactly
that axis. It was in virtual mode, so the motor was never driven.
`EAXIS_A` (logical device 0 = M1) has nothing wired and is in no group.

Units: 6400 steps = 36 u, so 1 u = 10 degrees. This is the /10 wrap
trick that lets the kinematics' +-180 cover +-1800 real degrees. The old
limits in those units: ID 7 180000 u/s (no limit at all); EAXIS_A 1800
u/s = 18000 deg/s = 50 turns/s, far too fast for a stepper.

`jobs/templates/set_group_a_axis.py` made ID 7 real with 36 u/s,
360 u/s^2, 3600 u/s^3 (one turn per second). `tools/a_axis_test.py`:

- A-only 90 deg: 0.46 s. 270 deg: 0.96 s. 360 deg: 1.21 s. No axis
  error. Positions right; +-180 and beyond work.
- The planner holds the axis limits: peak 36.0 u/s, 355 u/s^2.
- One G1 with X 30 mm + A 90 deg takes 0.46 s; the same X alone takes
  0.15 s. A in the group stretches every segment it rotates in to A's
  time. That is the owner's "much slower with A in the group".
- Open loop: lost steps cannot be seen by the PLC. Next steps:
  - on-site check of angle and direction, then a step-loss test (mark,
    or the Y HOME input);
  - raise the limits step by step;
  - move the rotations onto the long arm moves.

### A axis limits vs motion time per part (machine, 2026-09-29)

`tools/a_speed_compare.py`: the production G1 sequence of one part
(feed 2000, Cor 45), queued whole, timed to motion stop, 20 parts, the
same seeded angles. The delta arms are virtual; the A stepper turns.

| A limits (turns/s, turns/s^2) | A as in production | A on long moves only | A held at 0 |
|---|---|---|---|
| 1, 10 | 2.31 s | | 0.59 s |
| 3, 30 | 1.34 s | | 0.59 s |
| 5, 60 | 1.11 s | | 0.59 s |
| 8, 120 | 0.91 s | 0.84 s (one part 2.8 s: to check) | 0.59 s |
| no limit (virtual, the old setup) | 0.68 s | | 0.59 s |

- With A held, the time is 0.59 s under every setting. A in the group
  costs nothing while it does not turn. The owner's "twice as slow with
  A even when it does not turn" is not reproduced in this setup.
- The cost is the rotation under the A limits. Moving the rotations onto
  the long moves (no A-only G1) takes 0.08 s off at 8 turns/s.
- Next: the highest A limits the stepper holds (the bottom camera shows
  lost steps; the owner says a lost step only costs the part in hand,
  since every pick starts A from 0), then move the rotations in cycle.ts.
- Left on the machine: A real at 8 turns/s, 120 turns/s^2 (288 u/s,
  4320 u/s^2, jerk 86400 u/s^3).

Group without A (the owner remembers "twice as fast without A even when A
stays 0"): SpiderR exported (native XML, `doc_review/SpiderR_axisgroup_2026-09-29.export`),
the tool kinematics removed (`<Null Name="ToolKinematics" />`), imported
over the group (`app.import_native(Array[str]([file]), None, handler)`,
`NativeImportResolve.replace`), installed, measured, then the original
imported back and re-measured:

| group | motion per part, A never turning |
|---|---|
| without A (Kin_Tripod_Rotary only) | 0.591 s |
| with A (Kin_CAxis), A held at 0 | 0.590 s / 0.594 s after restoring |

No difference in this setup. The "A on long moves" outlier (one part
2.8 s) repeats exactly after the restore: a specific angle case, not
noise.
