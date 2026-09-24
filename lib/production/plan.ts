// Production-plan and tape bookkeeping, as pure functions.
//
// A plan is a list of segments along the tape: n > 0 packs n parts,
// n < 0 leaves |n| cells empty. Only the head segment is ever active; it
// shrinks as the tape advances past cells, and is dropped at 0.
//
// The top camera sees the first TAPE.VIEW_CELLS cells after the place
// position (slot 0..2). The tape only advances past a run of OK cells from
// slot 0, so every cell it moves past is packed.

import { TAPE } from './params';

export type Plan = number[];

/** What the top camera reported for the cells in view. */
export type SlotView = {
  /** 1 = the cell is empty. */
  isClear: number[];
  /** 1 = the cell holds a part that passed the top check. */
  isOk: number[];
};

/** Cells from slot 0 that hold an OK part, in a row. */
export function leadingOkRun(view: SlotView): number {
  let n = 0;
  while (n < view.isOk.length && view.isOk[n] === 1 && view.isClear[n] === 0) n++;
  return n;
}

/** First empty cell (the place target), or -1. */
export function firstClearSlot(view: SlotView): number {
  return view.isClear.findIndex((c) => c === 1);
}

/** First occupied cell whose part is NG (to pick back out), or -1. */
export function firstNgSlot(view: SlotView): number {
  return view.isClear.findIndex((c, i) => c === 0 && view.isOk[i] === 0);
}

/** Apply a tape advance of `cells` to the plan (in place, like the
 *  checkpoint handler always did) and return it. `kind` says which
 *  segment type the cells belong to; a mismatch is a caller bug. */
export function applyAdvance(plan: Plan, cells: number, kind: 'pack' | 'empty'): Plan {
  if (cells <= 0 || plan.length === 0) return plan;
  if (kind === 'empty') {
    if (plan[0] >= 0) throw new Error(`empty advance on a pack segment (${plan[0]})`);
    plan[0] += cells;
  } else {
    if (plan[0] <= 0) throw new Error(`pack advance on an empty segment (${plan[0]})`);
    plan[0] -= cells;
  }
  if (plan[0] === 0) plan.shift();
  return plan;
}

/** What the next cycle should do, decided before anything is picked. */
export type CycleAction =
  | { kind: 'done' }
  /** Advance an empty segment: `cells` in one tape step. */
  | { kind: 'skip_empty'; cells: number }
  /** The parts already in the tape may complete the head segment: wait
   *  for their top check and advance instead of picking another part. */
  | { kind: 'settle_segment' }
  /** Pick, inspect and place a part. */
  | { kind: 'pick' };

/**
 * @param plan            current plan
 * @param placedUncounted parts placed in the tape but not yet advanced past
 */
export function nextCycleAction(plan: Plan, placedUncounted: number): CycleAction {
  if (plan.length === 0) return { kind: 'done' };
  const head = plan[0];
  if (head < 0) return { kind: 'skip_empty', cells: Math.min(-head, TAPE.MAX_EMPTY_ADVANCE) };
  // Enough parts are already in the tape to finish this segment. Picking
  // another one now would inspect it and then toss it back ("segment count
  // reached": ~1.1 s per toss, 5 per run in the 2026-09-25 baseline).
  if (placedUncounted >= head) return { kind: 'settle_segment' };
  return { kind: 'pick' };
}

/**
 * How many cells a packed tape step may advance: the OK run in view,
 * capped by the segment and by TAPE.MAX_PACK_ADVANCE.
 */
export function packAdvance(plan: Plan, view: SlotView): number {
  if (plan.length === 0 || plan[0] <= 0) return 0;
  return Math.min(leadingOkRun(view), plan[0], TAPE.MAX_PACK_ADVANCE);
}

/**
 * Whether a part may go into `slot`. The tape only advances past OK cells
 * from slot 0, so every cell up to `slot` ends up packed: a slot past the
 * head segment's remaining count would pack one too many (chaos seed 38).
 */
export function slotFitsPlan(plan: Plan, slot: number, okAhead: number): boolean {
  if (plan.length === 0 || plan[0] <= 0) return false;
  if (slot < 0) return false;
  if (okAhead >= plan[0]) return false;      // segment already filled in the tape
  return slot + 1 <= plan[0];
}

/**
 * The remaining plan after `cellsDone` tape cells, from the whole plan.
 * Every segment moves the tape |n| cells (packed or empty alike), so the
 * cell count alone locates the job: this is how a plan kept by the PLC
 * (whole plan + cells advanced) turns back into the renderer's shape.
 */
export function remainingPlan(seg: readonly number[], cellsDone: number): Plan {
  let left = Math.max(0, Math.floor(cellsDone));
  const out: Plan = [];
  for (const n of seg) {
    const size = Math.abs(n);
    if (left >= size) { left -= size; continue; }
    const rest = size - left;
    left = 0;
    out.push(n > 0 ? rest : -rest);
  }
  return out;
}

/** Cells the tape has advanced through `plan` so far, given the whole plan. */
export function cellsDone(seg: readonly number[], remaining: readonly number[]): number {
  const total = (p: readonly number[]) => p.reduce((a, n) => a + Math.abs(n), 0);
  return total(seg) - total(remaining);
}
