import { describe, it, expect } from 'vitest';
import {
  applyAdvance, firstClearSlot, firstNgSlot, leadingOkRun, nextCycleAction,
  packAdvance, slotFitsPlan, remainingPlan, cellsDone,
} from './plan';
import { TAPE } from './params';

const view = (isOk: number[], isClear: number[]) => ({ isOk, isClear });

describe('tape view', () => {
  it('counts the OK run from slot 0', () => {
    expect(leadingOkRun(view([1, 1, 0], [0, 0, 1]))).toBe(2);
    expect(leadingOkRun(view([0, 1, 1], [0, 0, 0]))).toBe(0);   // NG in slot 0
    expect(leadingOkRun(view([1, 0, 0], [0, 1, 1]))).toBe(1);
  });
  it('finds the place target and the NG to pick out', () => {
    expect(firstClearSlot(view([1, 0, 0], [0, 1, 1]))).toBe(1);
    expect(firstClearSlot(view([1, 1, 1], [0, 0, 0]))).toBe(-1);
    expect(firstNgSlot(view([1, 0, 0], [0, 0, 1]))).toBe(1);
    expect(firstNgSlot(view([1, 0, 0], [0, 1, 1]))).toBe(-1);   // clear is not NG
  });
});

describe('applyAdvance', () => {
  it('counts packed cells down and drops a finished segment', () => {
    expect(applyAdvance([3, -2], 2, 'pack')).toEqual([1, -2]);
    expect(applyAdvance([1, -2], 1, 'pack')).toEqual([-2]);
  });
  it('counts empty cells up', () => {
    expect(applyAdvance([-10, 5], 10, 'empty')).toEqual([5]);
    expect(applyAdvance([-10, 5], 4, 'empty')).toEqual([-6, 5]);
  });
  it('refuses a mismatched advance', () => {
    expect(() => applyAdvance([3], 1, 'empty')).toThrow();
    expect(() => applyAdvance([-3], 1, 'pack')).toThrow();
  });
  it('ignores zero advances and an empty plan', () => {
    expect(applyAdvance([2], 0, 'pack')).toEqual([2]);
    expect(applyAdvance([], 1, 'pack')).toEqual([]);
  });
});

describe('nextCycleAction', () => {
  it('stops on an empty plan', () => {
    expect(nextCycleAction([], 0)).toEqual({ kind: 'done' });
  });
  it('skips a whole empty segment in one step', () => {
    expect(nextCycleAction([-10, 60], 0)).toEqual({ kind: 'skip_empty', cells: 10 });
    expect(nextCycleAction([-(TAPE.MAX_EMPTY_ADVANCE + 5)], 0))
      .toEqual({ kind: 'skip_empty', cells: TAPE.MAX_EMPTY_ADVANCE });
  });
  it('does not pick when the tape already holds the segment', () => {
    expect(nextCycleAction([1, -10], 1)).toEqual({ kind: 'settle_segment' });
    expect(nextCycleAction([2, -10], 3)).toEqual({ kind: 'settle_segment' });
    expect(nextCycleAction([2, -10], 1)).toEqual({ kind: 'pick' });
  });
});

describe('packAdvance', () => {
  it('is the OK run, capped by the segment and the step limit', () => {
    expect(packAdvance([60], view([1, 1, 1], [0, 0, 0]))).toBe(TAPE.MAX_PACK_ADVANCE);
    expect(packAdvance([1, -10], view([1, 1, 0], [0, 0, 1]))).toBe(1);
    expect(packAdvance([5], view([0, 1, 1], [0, 0, 0]))).toBe(0);
    expect(packAdvance([-3], view([1, 1, 1], [0, 0, 0]))).toBe(0);
  });
});

describe('slotFitsPlan', () => {
  it('rejects a slot past the remaining count (chaos seed 38)', () => {
    expect(slotFitsPlan([1], 1, 0)).toBe(false);
    expect(slotFitsPlan([1], 0, 0)).toBe(true);
    expect(slotFitsPlan([3], 2, 0)).toBe(true);
  });
  it('rejects when the OK run in the tape already fills the segment', () => {
    expect(slotFitsPlan([2, -5], 2, 2)).toBe(false);
    expect(slotFitsPlan([3, -5], 2, 2)).toBe(true);
  });
  it('rejects empty segments and a finished plan', () => {
    expect(slotFitsPlan([-4, 2], 0, 0)).toBe(false);
    expect(slotFitsPlan([], 0, 0)).toBe(false);
  });
});

describe('remainingPlan (PLC keeps whole plan + cells advanced)', () => {
  const seg = [1, -10, 60, -11, 1];
  it('locates the job from the cell count alone', () => {
    expect(remainingPlan(seg, 0)).toEqual(seg);
    expect(remainingPlan(seg, 1)).toEqual([-10, 60, -11, 1]);
    expect(remainingPlan(seg, 4)).toEqual([-7, 60, -11, 1]);
    expect(remainingPlan(seg, 11)).toEqual([60, -11, 1]);
    expect(remainingPlan(seg, 31)).toEqual([40, -11, 1]);
    expect(remainingPlan(seg, 83)).toEqual([]);
    expect(remainingPlan(seg, 999)).toEqual([]);
  });
  it('round-trips with cellsDone', () => {
    for (let c = 0; c <= 83; c++) {
      expect(cellsDone(seg, remainingPlan(seg, c))).toBe(c);
    }
  });
});
