# Production plan counts across stops and resumes (audit, 2026-10-06)

Owner's question: are the placed / empty cell counts of the production plan
guaranteed correct when production is stopped and resumed at arbitrary
moments? Read-only audit of the PLC ledger (GVL.PlanSeg / PlanCellsDone,
RETAIN; the reel tracker counts cells as the reel passes them,
AxisGroupSM.st ~1331-1339) and the UI (CalibPage run loop, lib/production
plan / cycle / tape / recovery).

## Verdict

Guaranteed only under conditions. The PLC ledger is sound: cells are counted
as the reel actually passes them, an interrupted tape move stays open and RUN
finishes it (REEL_RESUME) before resyncing, and for an unchanged plan the
PLC's count wins at the next RUN. Placed / empty are derived by position
(cell i of the tape = cell i of the plan, plan.ts:109-120). That holds only
if: the same plan is resumed with RUN; RETAIN is intact; the reel never
moves outside TAPE_CYCLE / REEL_RESUME; slots 0..2 are clear when an empty
segment starts; the top camera's verdicts are right. Ordinary operator
actions or a download break each of these, and nothing detects it.

## Stop points

| Stop | After the next RUN | Counts / tape wrong? |
|---|---|---|
| STOP at cycle_start or during a sensor hold | emptyNozzle, PLC count wins | no |
| arm fault / E-stop mid tape move | REEL_RESUME finishes the move, PLC wins | no (unless the PLC restarted: position lost) |
| fault mid pick, part on the nozzle | part to the NG bin, slots re-seen | no (one part scrapped) |
| fault mid place | top view finds the cell empty | yes if the part fell into another cell (operator must look) |
| UI crash / reload / link loss | PLC plan adopted | no, unless the plan is re-applied |
| PLC download / cold reset (RETAIN wiped) | the renderer's count (taken at send time) is pushed back | **yes** |
| power cycle | PLC wins; an open move becomes "position lost" | possibly (RETAIN on power loss unverified on this target) |
| ">" after a sensor hold | the held checkpoint runs again | no (refused after a PLC fault since d203055) |
| same plan re-applied, or plan changed | PLC progress reset to 0 | **yes** |
| last step of the plan fails | UI shows the plan finished | **yes** |
| top-camera NG on a placed part | picked out, refilled before the tape moves | no double count |

## Failure scenarios, most serious first

1. Re-applying (or changing) the plan silently resets the PLC's progress:
   every Apply gets a new plan_id (CalibPage.tsx setProductionPlan,
   `Date.now()`), the same-plan check fails, and PLAN_SET goes out with
   cells_done = 0 (CalibPage.tsx ~1679, DrainHostPackets PLAN_SET). Verified.
2. After a download (RETAIN wiped) the renderer's count, taken when each
   TAPE_CYCLE was sent, is pushed back: wrong after a run that ended on a NAK,
   a fault or a held STOP; an open reel move is forgotten.
3. Reel moves outside the plan are not counted: the manual ReelGo button
   (2 cells, locked only during a run), pulling the tape by hand. Every cell
   after that is shifted against the plan; the reel odometer is logged but
   never compared.
4. Empty segments advance without looking (cycle.ts ~168-176): a stray part
   in slots 0..2 leaves as an "empty" cell.
5. A failed last step shows the plan as finished (the end-of-run comparison
   runs only when runCycles returns); loading the next plan abandons the
   PLC's remaining cells.
6. "Position lost" is a dead end: RUN refuses, no UI for REEL_CLEAR, and its
   count is the operator's word.
7. The Count / speed display adds steps when sent and is never resynced;
   PLAN_SET zeroes PlanCellsPacked / PlanCellsEmpty even with cells_done > 0.
8. "Placed" means the top camera said OK.

## Tests

Offline: plan.test.ts (pure functions), recovery.test.ts (finishTapeMove
branches), cycle.test.ts (fake tape, whole steps: full plans, NG refill, NAK
stops the run, plan ending in an empty segment). abortTest.ts is a motion
redirect timing test, not a plan test. The sim harness (run_virtual chaos /
fault / estop with plan_check) checked the tape cell by cell (83 = 83) for
stop kinds 1-3, in simulation only.

Untested: STOP / hold at each checkpoint then a second run on the same tape;
syncPlanWithPlc (inside the component); RETAIN wipe / download; power cycle;
re-applying or changing the plan; manual ReelGo; stray parts before an empty
segment; a failed last step; the counters; anything on the machine.

## Smallest changes that would make the counts guaranteed

1. One ledger: the UI plan and counters derived from the PLC's cells_done on
   every TAPE_CYCLE ack (remainingPlan) instead of applyAdvance at send; a
   PLAN_GET whenever a run ends, however it ends, and on page load.
2. Apply: same segments + unfinished PLC plan -> keep the plan id, or ask
   "continue / restart"; confirm before abandoning an unfinished plan.
3. Before each empty advance and the first step of a new plan: the top view
   must show the leaving cells clear, else hold "remove part".
4. Outside reel moves: keep the odometer at each counted close in RETAIN,
   compare at RUN, refuse on a difference (or refuse ReelGo while a plan is
   unfinished).
5. RETAIN wipe: do not push the renderer's count without the operator's
   confirmation; deploy.py checks reel_open is false; test RETAIN over a
   power loss on the target.
6. Position lost: a UI to jog to the hole and send REEL_CLEAR with the count
   suggested from the odometer.
7. Tests for the above; move syncPlanWithPlc into lib and unit-test it.
