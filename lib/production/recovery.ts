// Getting the cell back into a known state before a run starts, after
// whatever ended the last one: a STOP, a servo / bus fault, an E-stop.
//
//  - The tape: a fault can stop the reel between two cells. The PLC
//    counts cells as the reel passes them and keeps the interrupted move
//    open (GVL.ReelMv*, PLAN_GET reel_*); every TAPE_CYCLE is refused
//    (reel_interrupted) until the rest of the move is done. `finishTapeMove`
//    does it (SYS REEL_RESUME), so the tape is back on a cell boundary and
//    the PLC's cell count -- which the plan resumes from -- is whole.
//    If the PLC restarted meanwhile the travel is unknown
//    (reel_pos_lost): that needs the operator, not a guess.
//  - The nozzle: a part may still hang on it (or the vacuum may have gone
//    with the fault). Nobody knows what it is -- a good part, or an NG
//    one being picked out of the tape -- so `emptyNozzle` drops it into
//    the part-NG bin: one part scrapped per interruption (decision D.11),
//    never an NG part back in the feeder (review 2026-09-26 #17).

import { cmd, type PlanState } from '../protocol';
import { GEOMETRY, TAPE } from './params';
import { tossTo } from './nozzle';
import type { Machine } from './machine';

export type TapeRecovery =
  | { state: 'none' }
  | { state: 'finished'; restMm: number; cellsDone: number }
  | { state: 'position_lost'; cells: number; counted: number }
  | { state: 'failed'; reason: string };

const POLL_MS = 50;

export async function finishTapeMove(m: Machine): Promise<TapeRecovery> {
  const st = await m.send(cmd.PlanGet()) as PlanState;
  if (!st?.reel_open) return { state: 'none' };
  if (st.reel_pos_lost) {
    return { state: 'position_lost', cells: st.reel_cells ?? 0, counted: st.reel_counted ?? 0 };
  }
  // A move still running (the fault came while it was under way and the
  // reel has not stopped yet): wait for the PLC to close or keep it open.
  const deadline = Date.now() + (TAPE.REEL_STOP_TIMEOUT_MS + 2000) * (m.timeScale?.() ?? 1);
  let now = st;
  while (now.reel_moving && Date.now() < deadline) {
    await m.delay(POLL_MS);
    now = await m.send(cmd.PlanGet()) as PlanState;
  }
  if (now.reel_moving) return { state: 'failed', reason: 'reel still moving' };
  if (!now.reel_open) return { state: 'none' };

  const rep = await m.send(cmd.ReelResume(TAPE.REEL_MOVE));
  const restMm = Number(rep?.rest_mm ?? 0);
  do {
    await m.delay(POLL_MS);
    now = await m.send(cmd.PlanGet()) as PlanState;
  } while (now.reel_moving && Date.now() < deadline + TAPE.REEL_STOP_TIMEOUT_MS);
  if (now.reel_open) {
    return { state: 'failed', reason: now.reel_moving ? 'reel still moving' : 'reel stopped short again' };
  }
  return { state: 'finished', restMm, cellsDone: now.cells_done };
}

/** Drop whatever the nozzle holds into the part-NG bin (from travel height). */
export async function emptyNozzle(m: Machine) {
  await m.send(cmd.G1({ Z: GEOMETRY.SAFE_Z }));
  await tossTo(m, GEOMETRY.TOSS_PART_NG);
}
