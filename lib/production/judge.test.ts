import { describe, it, expect } from 'vitest';
import { judgePlacement, placePose, type JudgeInput } from './judge';
import { GEOMETRY } from './params';

const top = (isOk: number[], isClear: number[], hole = { status: 1, x: 10, y: 5, mmpp: 0.01 }) =>
  ({ is_OK: isOk, is_clear: isClear, post_check_advCount: 0, locHole: hole });

const good = (over: Partial<JudgeInput> = {}): JudgeInput => ({
  reasons: [],
  partBin: 'part_ng',
  sideStatus: 1,
  btmPoseStatus: 1,
  armOffset: { X: 0.2, Y: -0.1 },
  btmMmpp: 0.02,
  top: top([1, 0, 0], [0, 1, 1]),
  plan: [60],
  ...over,
});

describe('judgePlacement', () => {
  it('places into the first empty slot, advancing the OK run', () => {
    const j = judgePlacement(good(), 1);
    expect(j.place).toBe(true);
    expect(j.placeSlot).toBe(1);
    expect(j.nextAdvance).toBe(1);
    expect(j.reasons).toEqual([]);
    expect(j.holeOffset).toEqual({ X: 0.1, Y: -0.1 });   // X top mmpp, Y bottom mmpp (see NOTE)
    expect(j.saveNames).toEqual(['OK_1', undefined, undefined]);
  });

  it('top timeout: nothing placed, picked or advanced; part back to the feeder', () => {
    const j = judgePlacement(good({ top: undefined }));
    expect(j.place).toBe(false);
    expect(j.partBin).toBe('feeder');
    expect(j.nextAdvance).toBe(0);
    expect(Number.isNaN(j.placeSlot) && Number.isNaN(j.ngSlot)).toBe(true);
  });

  it('inspection failures go to the part NG bin', () => {
    expect(judgePlacement(good({ sideStatus: 0 })).partBin).toBe('part_ng');
    const b = judgePlacement(good({ btmPoseStatus: 0 }));
    expect(b.partBin).toBe('part_ng');
    expect(b.compensationNg).toBe(true);
  });

  it('a correction out of range is not the part\'s fault: back to the feeder', () => {
    const j = judgePlacement(good({ armOffset: { X: 5, Y: 1 } }));
    expect(j.place).toBe(false);
    expect(j.partBin).toBe('feeder');
    expect(j.compensationNg).toBe(true);
  });

  it('a lost tape hole blocks placing; a far one also raises ERROR', () => {
    expect(judgePlacement(good({ top: top([1, 0, 0], [0, 1, 1], { status: 0, x: 0, y: 0, mmpp: 0 }) })).reasons)
      .toContain('slotHoleOffset is NaN');
    const far = judgePlacement(good({ top: top([1, 0, 0], [0, 1, 1], { status: 1, x: 200, y: 0, mmpp: 0.01 }) }));
    expect(far.holeTooFar).toBe(true);
    expect(far.place).toBe(false);
  });

  it('an NG part in the tape is picked out to the tape NG bin', () => {
    const j = judgePlacement(good({ top: top([1, 0, 0], [0, 0, 1]) }));
    expect(j.ngSlot).toBe(1);
    expect(j.tapeNgBin).toBe('tape_ng');
    expect(j.placeSlot).toBe(2);
  });

  it('no empty slot: part back to the feeder', () => {
    const j = judgePlacement(good({ top: top([1, 1, 1], [0, 0, 0]) }));
    expect(j.reasons).toContain('no slot to place');
    expect(j.partBin).toBe('feeder');
  });

  it('respects the plan: count reached, slot past the count, plan done', () => {
    const hit = judgePlacement(good({ plan: [1, -10], top: top([1, 0, 0], [0, 1, 1]) }));
    expect(hit.place).toBe(false);
    expect(hit.nextAdvance).toBe(1);
    const past = judgePlacement(good({ plan: [1], top: top([0, 0, 0], [0, 1, 1]) }));
    expect(past.reasons.some((r) => r.startsWith('production plan: slot 1'))).toBe(true);
    expect(judgePlacement(good({ plan: [] })).reasons).toContain('production plan is empty');
  });

  it('keeps reasons collected before the top result', () => {
    const j = judgePlacement(good({ reasons: ['SideCam measure failed'] }));
    expect(j.place).toBe(false);
    expect(j.reasons[0]).toBe('SideCam measure failed');
  });
});

describe('placePose', () => {
  it('offsets the slot by pitch and both corrections', () => {
    const p = placePose(2, { X: 0.5, Y: 0.2 }, { X: 0.1, Y: -0.1 }, 95);
    expect(p.X).toBeCloseTo(GEOMETRY.SLOT_LOCATION.X + 2 * GEOMETRY.SLOT_PITCH_MM - 0.5 + 0.1);
    expect(p.Y).toBeCloseTo(GEOMETRY.SLOT_LOCATION.Y - 0.2 - 0.1);
    expect(p).toMatchObject({ Z: GEOMETRY.SLOT_LOCATION.Z, A: 95 });
  });
});
