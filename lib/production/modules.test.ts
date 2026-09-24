// The cycle modules against a fake cell: which commands they send, in
// what order, with what parameters.
import { describe, it, expect } from 'vitest';
import type { Machine } from './machine';
import type { VisionCheck } from './io';
import { IO_PINS, bit } from './io';
import { GEOMETRY, NOZZLE, TAPE } from './params';
import { tapeStep } from './tape';
import { pickFromFeeder, placePart, tossTo, pickFromTape } from './nozzle';
import { refillFeeder } from './feeder';

type Sent = { pkt: any; waited: boolean };

function fakeMachine(opts: { replies?: Record<string, any>; vision?: Partial<Record<VisionCheck, any>> } = {}) {
  const sent: Sent[] = [];
  const marks: number[] = [];
  const feederCalls: string[] = [];
  const m: Machine = {
    send: async (pkt: any) => { sent.push({ pkt, waited: true }); return opts.replies?.[pkt.cmd] ?? { ack: true }; },
    sendNoWait: (pkt: any) => { sent.push({ pkt, waited: false }); },
    mark: (c) => { if (c !== undefined) marks.push(c); },
    waitVision: async (c) => opts.vision?.[c],
    sendVision: async () => ({}),
    feeder: {
      von: (ch) => feederCalls.push('von' + ch),
      voff: (ch) => feederCalls.push('voff' + ch),
      top_light_on: () => feederCalls.push('light_on'),
      top_light_off: () => feederCalls.push('light_off'),
    },
    delay: async () => {},
  };
  return { m, sent, marks, feederCalls };
}

const view = { is_clear: [0, 1, 1], is_OK: [1, 0, 0], locHole: { status: 1, x: 0, y: 0, mmpp: 0.01 } };

describe('tapeStep', () => {
  it('sends one TAPE_CYCLE with the plan cells and kind, then waits for the top result', async () => {
    const { m, sent } = fakeMachine({ replies: { TAPE_CYCLE: { ack: true, cells_done: 7 } }, vision: { top: view } });
    const r = await tapeStep(m, { cells: 2, kind: 'pack', trigger: { motion_id_offset: 0, motion_progress: 0 } });
    expect(sent).toHaveLength(1);
    const p = sent[0].pkt;
    expect(p.cmd).toBe('TAPE_CYCLE');
    expect(p.Distance).toBe(2 * TAPE.REEL_CELL_DISTANCE);
    expect(p.cells).toBe(2);
    expect(p.kind).toBe(1);
    expect(p.tx).toBe(GEOMETRY.SLOT_LOCATION.X + GEOMETRY.SLOT_PITCH_MM);
    expect(p.td).toBe(TAPE.TOP_CAM_CLEAR_MM);
    expect(p.pin_op_seq).toHaveLength(12);
    expect(r.cellsDone).toBe(7);
    expect(r.planErr).toBe(false);
    expect(r.view?.is_OK).toEqual([1, 0, 0]);
  });
  it('marks empty advances, and a shot without an advance carries no plan fields', async () => {
    const { m, sent } = fakeMachine({ vision: { top: view } });
    await tapeStep(m, { cells: 10, kind: 'empty' });
    expect(sent[0].pkt.kind).toBe(2);
    await tapeStep(m, { cells: 0, kind: 'pack' });
    expect(sent[1].pkt.Distance).toBe(0);
    expect('cells' in sent[1].pkt).toBe(false);
  });
  it('reports a vision timeout and a PLC plan disagreement', async () => {
    const { m } = fakeMachine({ replies: { TAPE_CYCLE: { ack: true, cells_done: 3, plan_err: true } } });
    const r = await tapeStep(m, { cells: 1, kind: 'pack' });
    expect(r.view).toBeUndefined();
    expect(r.planErr).toBe(true);
  });
});

describe('nozzle', () => {
  const SUCK = bit(IO_PINS.O.Nozzle_suck);
  const BLOW = bit(IO_PINS.O.Nozzle_blow);

  it('picks: approach, down, suction on, dwell, up', async () => {
    const { m, sent } = fakeMachine();
    await pickFromFeeder(m, { X: 1, Y: 2, Z: 3, A: 45 });
    expect(sent.map((s) => s.pkt.cmd)).toEqual(['G1', 'G1', 'M4', 'G4', 'G1']);
    expect(sent[1].pkt.Z).toBe(3 + GEOMETRY.PICK_Z_LIFT);
    expect(sent[2].pkt.pin_op_seq).toEqual([0, SUCK, SUCK]);      // suction on
    expect(sent[4].pkt.Z).toBe(GEOMETRY.SAFE_Z);
  });
  it('places: approach, checkpoint, down, release with a vacuum break, up', async () => {
    const { m, sent } = fakeMachine();
    const order: string[] = [];
    await placePart(m, { X: 1, Y: 2, Z: -11, A: 90 }, async () => { order.push('checkpoint@' + sent.length); });
    expect(order).toEqual(['checkpoint@1']);           // after the approach only
    expect(sent.map((s) => s.pkt.cmd)).toEqual(['G1', 'G1', 'G4', 'M4', 'M4', 'G4', 'G1']);
    expect(sent[0].pkt.Z).toBe(-11 + 5);
    expect(sent[3].pkt.pin_op_seq).toEqual([0, SUCK, 0]);          // suction off
    // vacuum break: blow on, off again after PLACE_BLOW_MS
    expect(sent[4].pkt.pin_op_seq).toEqual([0, BLOW, BLOW, NOZZLE.PLACE_BLOW_MS, BLOW, 0]);
  });
  it('tosses at travel height and drops without waiting', async () => {
    const { m, sent } = fakeMachine();
    await tossTo(m, { X: -60, Y: 60 });
    expect(sent[0]).toMatchObject({ waited: true, pkt: { cmd: 'G1', X: -60, Y: 60, Z: GEOMETRY.SAFE_Z } });
    expect(sent.slice(1).every((s) => !s.waited)).toBe(true);
  });
  it('picks an NG part out of the tape below the slot height', async () => {
    const { m, sent } = fakeMachine();
    await pickFromTape(m, 50, -80);
    expect(sent[1].pkt.Z).toBe(GEOMETRY.SLOT_LOCATION.Z - NOZZLE.NG_PICK_DEPTH_MM);
  });
});

describe('feeder', () => {
  it('shakes, brakes, shoots and keeps only clear parts', async () => {
    const parts = [
      { x: 1, y: 1, ang: 3, inner: 1, outer: 1 },
      { x: 2, y: 2, ang: 4, inner: 1, outer: 0 },
    ];
    const { m, feederCalls } = fakeMachine({ vision: { feeder: parts } });
    const got = await refillFeeder(m);
    expect(got).toEqual([{ x: 1, y: 1, angle_deg: 3, surround_clear: 1, center_clear: 1 }]);
    expect(feederCalls.slice(0, 2)).toEqual(['von10', 'voff10']);
  });
  it('treats a vision timeout as an empty plate', async () => {
    const { m } = fakeMachine({ vision: {} });
    expect(await refillFeeder(m)).toEqual([]);
  });
});
