// The whole cycle loop against a fake cell with a tape model: plans run
// to the end and the tape must hold exactly what the plan says. No PLC,
// no sim, milliseconds.
import { describe, it, expect } from 'vitest';
import type { Machine } from './machine';
import { runCycles, type CycleContext } from './cycle';
import { applyAdvance } from './plan';
import { FEEDER, GEOMETRY, NOZZLE, TAPE } from './params';
import { IO_PINS, bit } from './io';

const SUCK = bit(IO_PINS.O.Nozzle_suck);

/** Tape cell: '' empty, 'P' good part, 'X' part the top camera calls NG. */
type Cell = '' | 'P' | 'X';

function fakeCell(opts: {
  ngAt?: number[];
  /** The feeder camera never finds a part. */
  feederEmpty?: boolean;
  /** The top camera never finds the tape hole (every correction fails). */
  holeLost?: boolean;
  /** An NG part the nozzle cannot get out of the tape. */
  stuckNg?: boolean;
} = {}) {
  const tape: Cell[] = [];
  let pos = 0;                        // tape index under slot 0
  let placed = 0;                     // parts placed so far
  const pose = { X: 0, Y: 0, Z: 0 };
  const log: string[] = [];
  const cell = (k: number) => tape[pos + k] ?? '';
  const slotAt = (x: number) => Math.round((x - GEOMETRY.SLOT_LOCATION.X) / GEOMETRY.SLOT_PITCH_MM);

  const m: Machine = {
    send: async (pkt: any) => {
      if (pkt.cmd === 'G1') {
        for (const k of ['X', 'Y', 'Z'] as const) if (typeof pkt[k] === 'number') pose[k] = pkt[k];
      } else if (pkt.cmd === 'TAPE_CYCLE') {
        pos += Math.round(pkt.Distance / TAPE.REEL_CELL_DISTANCE);
        return { ack: true, cells_done: pos };
      } else if (pkt.cmd === 'M4' && Array.isArray(pkt.pin_op_seq)) {
        const [, mask, state] = pkt.pin_op_seq;
        const atTape = pose.Z <= GEOMETRY.SLOT_LOCATION.Z + 0.01;
        if (mask === SUCK && state === 0 && atTape) {              // release into a slot
          const k = slotAt(pose.X);
          tape[pos + k] = (opts.ngAt ?? []).includes(placed) ? 'X' : 'P';
          placed++;
          log.push('place@' + (pos + k));
        } else if (mask === SUCK && state === SUCK && pose.Z < GEOMETRY.SLOT_LOCATION.Z) {  // NG pick
          const k = slotAt(pose.X);
          log.push('ngpick@' + (pos + k));
          if (!opts.stuckNg) tape[pos + k] = '';
        }
      }
      return { ack: true };
    },
    sendNoWait: (pkt) => { void m.send(pkt); },
    queue: async (pkt) => { void m.send(pkt); },
    mark: () => {},
    waitVision: async (c) => {
      if (c === 'top') {
        const cells = [0, 1, 2].map(cell);
        return { is_clear: cells.map((v) => (v === '' ? 1 : 0)), is_OK: cells.map((v) => (v === 'P' ? 1 : 0)),
          locHole: { status: opts.holeLost ? 0 : 1, x: 0, y: 0, mmpp: 0.01 } };
      }
      if (c === 'side') return { status: 1, facing: 0, measure: { status: 1 } };
      if (c === 'btm') return { status: 1, obj_pose: { x: 0, y: 0, ang: 0, status: 1 } };
      if (opts.feederEmpty) { log.push('refill'); return []; }
      return Array.from({ length: 5 }, (_, k) => ({ x: k, y: k, ang: 0, inner: 1, outer: 1 }));
    },
    sendVision: async () => ({}),
    feeder: { von: () => {}, voff: () => {}, top_light_on: () => {}, top_light_off: () => {} },
    delay: async () => {},
  };
  const tapeString = () => Array.from({ length: pos }, (_, k) => (tape[k] === 'P' ? 'P' : tape[k] === 'X' ? 'X' : '_')).join('');
  return { m, tapeString, log };
}

/** The page's checkpoint handler, reduced to the plan bookkeeping. An
 *  ERROR checkpoint ends the run, as the page does; its text lands in
 *  `errors`. */
function context(m: Machine, plan: number[], errors: string[] = []): CycleContext {
  const checkpoint = async (name: string, data: any) => {
    if (name === 'cycle_start' || name === 'GetProductionPlan') return { production_plan: plan };
    if (name.startsWith('[STEP][REEL ADV]')) applyAdvance(plan, data.adv_count, data.type);
    if (name === 'ERROR') { errors.push(data?.errorString); throw new Error(data?.errorString); }
    return undefined;
  };
  return {
    m, checkpoint, plan: () => plan,
    predict: (p) => ({ X: p.x, Y: p.y, Z: 0 }),
    armOffset: () => ({ X: 0, Y: 0 }),
    btmCenter: { X: 0, Y: 0 },
  };
}

const expected = (plan: number[]) => plan.map((n) => (n > 0 ? 'P' : '_').repeat(Math.abs(n))).join('');

describe('runCycles against a fake tape', () => {
  for (const plan of [[1, -10, 60, -11, 1], [4, -5, 3, -3, 2], [2], [-3, 2], [1, -1, 1, -1, 1]]) {
    it(`packs ${plan.join(',')} exactly`, async () => {
      const f = fakeCell();
      const r = await runCycles(context(f.m, [...plan]));
      expect(r.ended).toBe('plan_done');
      expect(f.tapeString()).toBe(expected(plan));
    });
  }

  it('picks top-check NG parts back out and replaces them', async () => {
    const plan = [5, -2, 3];
    const f = fakeCell({ ngAt: [1, 4] });
    await runCycles(context(f.m, [...plan]));
    expect(f.tapeString()).toBe(expected(plan));
    expect(f.log.filter((l) => l.startsWith('ngpick')).length).toBe(2);
  });

  it('never places more parts than the plan packs (no plan tosses needed)', async () => {
    const plan = [1, -10, 60, -11, 1];
    const f = fakeCell();
    await runCycles(context(f.m, [...plan]));
    expect(f.log.filter((l) => l.startsWith('place')).length).toBe(62);
  });
});

describe('runCycles stops instead of repeating a failure forever', () => {
  it('feeder never finds a part', async () => {
    const f = fakeCell({ feederEmpty: true });
    const errors: string[] = [];
    const r = await runCycles(context(f.m, [5], errors));
    expect(r.ended).toBe('error_stop');
    expect(errors[0]).toMatch(/no pickable part/);
    expect(f.log.filter((l) => l === 'refill').length).toBe(FEEDER.MAX_EMPTY_REFILLS);
  });

  it('every part tossed (tape hole never found)', async () => {
    const f = fakeCell({ holeLost: true });
    const errors: string[] = [];
    const r = await runCycles(context(f.m, [5], errors));
    expect(r.ended).toBe('error_stop');
    expect(errors[0]).toMatch(/in a row tossed/);
    expect(f.log.filter((l) => l.startsWith('place')).length).toBe(0);
  });

  it('an NG part that will not come out of the tape', async () => {
    const f = fakeCell({ ngAt: [1], stuckNg: true });
    const errors: string[] = [];
    const r = await runCycles(context(f.m, [5], errors));
    expect(r.ended).toBe('error_stop');
    expect(errors[0]).toMatch(/not removed/);
    expect(f.log.filter((l) => l.startsWith('ngpick')).length).toBe(NOZZLE.MAX_NG_PICKS_IN_A_ROW + 1);
  });
});
