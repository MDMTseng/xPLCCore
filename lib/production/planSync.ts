// The production plan between the renderer and the PLC (2026-10-06, owner's
// question "are the placed / empty counts right across stops and resumes?",
// doc_review/plan_counting_audit_2026-10-06.md).
//
// The PLC is the ledger: it keeps the whole plan and the tape cells advanced
// since PLAN_SET in RETAIN, counted as the reel actually passes them. The
// renderer advances its own copy when it SENDS a tape step (the cycle is
// pipelined and decides on it), so its copy can run ahead of the tape; it
// is resynced from the PLC whenever a run ends and on reconnect (pullPlan),
// and RUN decides with decideSync. Placed / empty counts are derived from
// the PLC's cell count by position (countsAt), never accumulated.

import { remainingPlan, cellsDone } from './plan';

export type PlcPlan = {
  plan_id: number;
  seg: number[];
  cells_done: number;
  reel_open?: boolean;
  reel_pos_lost?: boolean;
};

export type UiPlan = {
  /** The whole plan as applied, or undefined (renderer restarted). */
  original?: number[];
  /** What is left of it on the renderer's side. */
  remaining?: number[];
  id?: number;
  /** The operator asked to start this plan from its first cell. */
  restart?: boolean;
};

export type SyncDecision =
  /** Same plan: the PLC's count wins. */
  | { kind: 'plc'; id: number; remaining: number[] }
  /** The renderer has no plan: take the PLC's unfinished one. */
  | { kind: 'resume'; id: number; original: number[]; remaining: number[] }
  | { kind: 'none' }
  /** Send this plan to the PLC with `done` cells already advanced. */
  | { kind: 'set'; seg: number[]; id: number; done: number }
  /** The PLC has no plan (RETAIN wiped by a download / cold reset) but the
   *  renderer counts progress: only the operator can say the tape is there. */
  | { kind: 'confirm_push'; seg: number[]; id: number; done: number }
  /** The PLC holds a different unfinished plan: replacing it abandons
   *  `plcCellsLeft` cells; only the operator can decide. */
  | { kind: 'confirm_abandon'; seg: number[]; id: number; done: number; plcCellsLeft: number };

const total = (p: readonly number[]) => p.reduce((a, n) => a + Math.abs(n), 0);
const sameSeg = (a: readonly number[], b: readonly number[]) => a.length === b.length && a.every((n, i) => n === b[i]);

/** Cells the PLC's plan still has to go (0 = finished or none). */
export function plcCellsLeft(plc: PlcPlan): number {
  return Math.max(0, total(plc.seg ?? []) - Math.max(0, plc.cells_done ?? 0));
}

/** What RUN does with the plan. */
export function decideSync(ui: UiPlan, plc: PlcPlan): SyncDecision {
  const seg = plc.seg ?? [];
  const plcRemaining = remainingPlan(seg, plc.cells_done ?? 0);
  const left = plcCellsLeft(plc);
  const original = ui.original && ui.original.length > 0 ? ui.original : undefined;

  if (!original) {
    if (left > 0) return { kind: 'resume', id: plc.plan_id, original: [...seg], remaining: plcRemaining };
    return { kind: 'none' };
  }
  const id = ui.id ?? 0;
  const done = ui.restart ? 0 : cellsDone(original, ui.remaining ?? original);

  if (!ui.restart && sameSeg(seg, original) && seg.length > 0) {
    // Same segments: the same plan, also when it was applied again (a new
    // id used to reset the PLC's progress silently, audit #1). Continue it
    // under the PLC's id; "Restart" is the explicit way to start over.
    if (plc.plan_id === id || left > 0) return { kind: 'plc', id: plc.plan_id, remaining: plcRemaining };
  }
  if (left > 0) return { kind: 'confirm_abandon', seg: [...original], id, done, plcCellsLeft: left };
  if (seg.length === 0 && done > 0) return { kind: 'confirm_push', seg: [...original], id, done };
  return { kind: 'set', seg: [...original], id, done };
}

/** At a run's end or a reconnect: the remaining plan from the PLC's count
 *  when it is the renderer's plan, else undefined (leave it alone). */
export function pullPlan(ui: UiPlan, plc: PlcPlan): number[] | undefined {
  const original = ui.original;
  if (!original || original.length === 0) return undefined;
  if (plc.plan_id !== ui.id || !sameSeg(plc.seg ?? [], original)) return undefined;
  return remainingPlan(original, plc.cells_done ?? 0);
}

/** Placed / empty cells after `done` cells of `seg`, by position. */
export function countsAt(seg: readonly number[], done: number): { packed: number; empty: number; done: number; total: number } {
  let left = Math.max(0, Math.floor(done));
  let packed = 0;
  let empty = 0;
  for (const n of seg) {
    const take = Math.min(left, Math.abs(n));
    if (n > 0) packed += take; else empty += take;
    left -= take;
    if (left === 0) break;
  }
  return { packed, empty, done: packed + empty, total: total(seg) };
}
