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
| 12 | Any vision-link status change rejects pending vision waits with a misleading message; vision requests have no timeout. | `PluginHello.tsx`, `CalibPage.tsx` |  |
| 13 | Every plan-sync failure is swallowed as "older PLC"; plan mismatches are console only. | `CalibPage.tsx` | Fixed: only an unknown-command NAK (older PLC program) falls back; other plan-sync / tape-check failures stop the RUN. Plan-book differences at the end of a run are shown to the operator. |
| 14 | Manual debug buttons stay live during a run (steal vision replies, move the reel). | `CalibPage.tsx` | Fixed: the manual/debug section ignores clicks while a run is on. |
| 15 | A NAK'd pack tape step surfaces only after pick and inspection (part on nozzle); the renderer plan is decremented before the PLC acks. | `cycle.ts` | Fixed: a failed tape step (pack or empty) is raised before the next pick; a plan ending in an empty segment waits for its last step. (Renderer plan still decremented before the ack: the PLC's count is the truth at the next RUN.) |
| 16 | Feeder Modbus writes are not awaited (failures unseen); a refill in flight is not settled when the loop ends. | `feeder.ts`, `PluginHello.tsx` |  |
| 17 | An NG part can be re-judged into the feeder bin; emptyNozzle returns tape NG parts to the feeder. | `judge.ts`, `recovery.ts` |  |
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
