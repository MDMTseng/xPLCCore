import { describe, it, expect } from 'vitest';
import { decideSync, pullPlan, countsAt, plcCellsLeft } from './planSync';

const PLAN = [-2, 10, -3, 5];     // 20 cells: 2 empty, 10 packed, 3 empty, 5 packed

describe('decideSync', () => {
  it('same plan and id: the PLC count wins', () => {
    const d = decideSync({ original: PLAN, remaining: [5], id: 7 }, { plan_id: 7, seg: PLAN, cells_done: 6 });
    expect(d).toEqual({ kind: 'plc', id: 7, remaining: [6, -3, 5] });
  });

  it('the same plan applied again continues the PLC progress (audit #1)', () => {
    // re-applied: new id, renderer back at the start
    const d = decideSync({ original: PLAN, remaining: [...PLAN], id: 99 }, { plan_id: 7, seg: PLAN, cells_done: 6 });
    expect(d).toEqual({ kind: 'plc', id: 7, remaining: [6, -3, 5] });
  });

  it('Restart starts the same plan from cell 0', () => {
    const d = decideSync({ original: PLAN, remaining: [...PLAN], id: 99, restart: true }, { plan_id: 7, seg: PLAN, cells_done: 6 });
    expect(d).toEqual({ kind: 'confirm_abandon', seg: PLAN, id: 99, done: 0, plcCellsLeft: 14 });
    const fin = decideSync({ original: PLAN, remaining: [...PLAN], id: 99, restart: true }, { plan_id: 7, seg: PLAN, cells_done: 20 });
    expect(fin).toEqual({ kind: 'set', seg: PLAN, id: 99, done: 0 });
  });

  it('a different plan over an unfinished one needs the operator', () => {
    const d = decideSync({ original: [4], remaining: [4], id: 1 }, { plan_id: 7, seg: PLAN, cells_done: 6 });
    expect(d).toEqual({ kind: 'confirm_abandon', seg: [4], id: 1, done: 0, plcCellsLeft: 14 });
  });

  it('a different plan over a finished one is simply set', () => {
    const d = decideSync({ original: [4], remaining: [4], id: 1 }, { plan_id: 7, seg: PLAN, cells_done: 20 });
    expect(d).toEqual({ kind: 'set', seg: [4], id: 1, done: 0 });
  });

  it('PLC wiped (no plan) with renderer progress needs the operator (audit #2)', () => {
    const d = decideSync({ original: PLAN, remaining: [6, -3, 5], id: 7 }, { plan_id: 0, seg: [], cells_done: 0 });
    expect(d).toEqual({ kind: 'confirm_push', seg: PLAN, id: 7, done: 6 });
  });

  it('PLC wiped, renderer at the start: just set', () => {
    const d = decideSync({ original: PLAN, remaining: [...PLAN], id: 7 }, { plan_id: 0, seg: [], cells_done: 0 });
    expect(d).toEqual({ kind: 'set', seg: PLAN, id: 7, done: 0 });
  });

  it('renderer restarted: resume the PLC plan, or nothing', () => {
    expect(decideSync({}, { plan_id: 7, seg: PLAN, cells_done: 6 }))
      .toEqual({ kind: 'resume', id: 7, original: PLAN, remaining: [6, -3, 5] });
    expect(decideSync({}, { plan_id: 7, seg: PLAN, cells_done: 20 })).toEqual({ kind: 'none' });
  });
});

describe('pullPlan', () => {
  it('takes the PLC count for the renderer plan', () => {
    // renderer ran ahead (step sent, reel never moved)
    expect(pullPlan({ original: PLAN, remaining: [], id: 7 }, { plan_id: 7, seg: PLAN, cells_done: 18 })).toEqual([2]);
  });
  it('leaves another plan alone', () => {
    expect(pullPlan({ original: PLAN, remaining: [], id: 7 }, { plan_id: 8, seg: PLAN, cells_done: 18 })).toBeUndefined();
    expect(pullPlan({ original: PLAN, remaining: [], id: 7 }, { plan_id: 7, seg: [4], cells_done: 1 })).toBeUndefined();
  });
});

describe('countsAt', () => {
  it('derives placed and empty cells by position', () => {
    expect(countsAt(PLAN, 0)).toEqual({ packed: 0, empty: 0, done: 0, total: 20 });
    expect(countsAt(PLAN, 1)).toEqual({ packed: 0, empty: 1, done: 1, total: 20 });
    expect(countsAt(PLAN, 6)).toEqual({ packed: 4, empty: 2, done: 6, total: 20 });
    expect(countsAt(PLAN, 13)).toEqual({ packed: 10, empty: 3, done: 13, total: 20 });
    expect(countsAt(PLAN, 20)).toEqual({ packed: 15, empty: 5, done: 20, total: 20 });
    expect(countsAt(PLAN, 25)).toEqual({ packed: 15, empty: 5, done: 20, total: 20 });
  });
  it('plcCellsLeft', () => {
    expect(plcCellsLeft({ plan_id: 1, seg: PLAN, cells_done: 6 })).toBe(14);
    expect(plcCellsLeft({ plan_id: 1, seg: [], cells_done: 0 })).toBe(0);
  });
});
