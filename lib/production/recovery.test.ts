// Tape and nozzle recovery against a fake PLC: an interrupted tape move is
// finished with REEL_RESUME before the run, a lost position is left to the
// operator, and the nozzle is emptied over the part-NG bin.
import { describe, it, expect } from 'vitest';
import type { Machine } from './machine';
import { GEOMETRY, TAPE } from './params';
import { finishTapeMove, emptyNozzle } from './recovery';

/** A PLC whose PLAN_GET answers come from `states` in turn (the last repeats). */
function fakePlc(states: any[], resumeReply: any = { ack: true, rest_mm: 3.2 }) {
  const sent: any[] = [];
  let i = 0;
  const m: Machine = {
    send: async (pkt: any) => {
      sent.push(pkt);
      if (pkt.cmd === 'PLAN_GET') return states[Math.min(i++, states.length - 1)];
      if (pkt.cmd === 'REEL_RESUME') return resumeReply;
      return { ack: true };
    },
    sendNoWait: (pkt: any) => { sent.push(pkt); },
    queue: async (pkt: any) => { sent.push(pkt); },
    mark: () => {},
    waitVision: async () => undefined,
    sendVision: async () => ({}),
    feeder: { von: () => {}, voff: () => {}, top_light_on: () => {}, top_light_off: () => {} },
    delay: async () => {},
  };
  return { m, sent };
}

const base = { plan_id: 1, plan_rev: 1, seg: [1, -10, 60], cells_packed: 0, cells_empty: 0, mismatch: 0 };

describe('finishTapeMove', () => {
  it('does nothing when no tape move is open (or on an older PLC)', async () => {
    const { m, sent } = fakePlc([{ ...base, cells_done: 5, reel_open: false }]);
    expect(await finishTapeMove(m)).toEqual({ state: 'none' });
    expect(sent.map((p) => p.cmd)).toEqual(['PLAN_GET']);
    const old = fakePlc([{ ...base, cells_done: 5 }]);
    expect((await finishTapeMove(old.m)).state).toBe('none');
  });

  it('finishes an interrupted move with the normal reel dynamics and waits for it', async () => {
    const { m, sent } = fakePlc([
      { ...base, cells_done: 11, reel_open: true, reel_moving: false, reel_rest_mm: 3.2 },
      { ...base, cells_done: 11, reel_open: true, reel_moving: true },
      { ...base, cells_done: 12, reel_open: false, reel_moving: false },
    ]);
    const r = await finishTapeMove(m);
    expect(r).toEqual({ state: 'finished', restMm: 3.2, cellsDone: 12 });
    const resume = sent.find((p) => p.cmd === 'REEL_RESUME');
    expect(resume).toMatchObject({ type: 'SYS', ...TAPE.REEL_MOVE });
  });

  it('first lets a move still under way stop', async () => {
    const { m, sent } = fakePlc([
      { ...base, cells_done: 3, reel_open: true, reel_moving: true },
      { ...base, cells_done: 4, reel_open: false, reel_moving: false },
    ]);
    expect((await finishTapeMove(m)).state).toBe('none');
    expect(sent.some((p) => p.cmd === 'REEL_RESUME')).toBe(false);
  });

  it('leaves a lost position to the operator', async () => {
    const { m, sent } = fakePlc([{ ...base, cells_done: 20, reel_open: true, reel_pos_lost: true, reel_cells: 10, reel_counted: 4 }]);
    expect(await finishTapeMove(m)).toEqual({ state: 'position_lost', cells: 10, counted: 4 });
    expect(sent.some((p) => p.cmd === 'REEL_RESUME')).toBe(false);
  });

  it('reports a resume that stopped short again', async () => {
    const { m } = fakePlc([
      { ...base, cells_done: 11, reel_open: true, reel_moving: false },
      { ...base, cells_done: 11, reel_open: true, reel_moving: false },
    ]);
    expect(await finishTapeMove(m)).toEqual({ state: 'failed', reason: 'reel stopped short again' });
  });
});

describe('emptyNozzle', () => {
  it('lifts, goes over the part-NG bin and drops', async () => {
    const { m, sent } = fakePlc([]);
    await emptyNozzle(m);
    expect(sent[0]).toMatchObject({ cmd: 'G1', Z: GEOMETRY.SAFE_Z });
    expect(sent[1]).toMatchObject({ cmd: 'G1', X: GEOMETRY.TOSS_PART_NG.X, Y: GEOMETRY.TOSS_PART_NG.Y, Z: GEOMETRY.SAFE_Z });
    expect(sent.filter((p) => p.cmd === 'M4')).toHaveLength(2);   // suck off, blow
  });
});
