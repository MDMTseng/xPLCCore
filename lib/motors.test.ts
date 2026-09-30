import { describe, it, expect } from 'vitest';
import { A_AXIS, MOTORS, applyLimits, runALadder, runATest, scaleLimits, toAxisUnits, turnsPerUnit } from './motors';

/** A fake PLC: FSM, A's limits, the G1s it got. */
function fakePlc(o: { armOk?: boolean } = {}) {
  let st = 'UnInited';
  const lim = { lv: 288, la: 4320, ld: 4320, lj: 86400 };
  const cfg = { ...lim };
  const g1s: any[] = [];
  const sent: string[] = [];
  const send = async (pkt: any) => {
    sent.push(pkt.cmd === 'GA_EV' ? `GA_EV${pkt.ev}` : pkt.cmd);
    if (pkt.cmd === 'GA_EV') {
      const next: Record<number, string> = { 2: 'Powered', 4: 'GroupEnabled', 7: 'Ready', 8: 'UnInited' };
      if (pkt.ev !== 0 && next[pkt.ev]) st = next[pkt.ev];
      return { ack: true, st_str: st };
    }
    if (pkt.cmd === 'SET_AXIS_LIMITS') {
      if (st !== 'UnInited') throw new Error('SET_AXIS_LIMITS: not_uninited');
      if (pkt.restore) Object.assign(lim, cfg);
      else Object.assign(lim, { lv: pkt.v ?? lim.lv, la: pkt.a ?? lim.la, ld: pkt.d ?? lim.ld, lj: pkt.j ?? lim.lj });
      return { ack: true, ...lim };
    }
    if (pkt.cmd === 'AXIS_INFO') {
      return { ack: true, axis: pkt.axis, ...lim, cv: cfg.lv, ca: cfg.la, cd: cfg.ld, cj: cfg.lj, sf: 177.78,
        pv: lim.lv, pa: lim.la * 0.99, pj: lim.lj, arm_ok: o.armOk ?? true, arm_x: 1, arm_y: 2, arm_z: 12 };
    }
    if (pkt.cmd === 'G1') { if (st !== 'Ready') throw new Error('G1: group_not_ready'); g1s.push(pkt); }
    return { ack: true };
  };
  return { send, g1s, sent, lim, state: () => st };
}

describe('motor limits', () => {
  it('scales for the same motion f times faster, or all by f', () => {
    const l = { v: 100, a: 1000, d: 1000, j: 10000 };
    expect(scaleLimits(l, 2, 'time')).toEqual({ v: 200, a: 4000, d: 4000, j: 80000 });
    expect(scaleLimits(l, 2, 'all')).toEqual({ v: 200, a: 2000, d: 2000, j: 20000 });
  });

  it('converts A degrees to axis units (1 u = 10 deg) and reads motor turns', () => {
    expect(toAxisUnits(MOTORS[A_AXIS], { v: 2880, a: 43200, d: 43200, j: 864000 })).toEqual({ v: 288, a: 4320, d: 4320, j: 86400 });
    expect(1 / turnsPerUnit(MOTORS[A_AXIS], 6400 / 36)).toBeCloseTo(36);          // 36 u per motor turn
    expect(1 / turnsPerUnit(MOTORS[0], 2 ** 24 * 31 / 360)).toBeCloseTo(360 / 31); // joint deg per motor turn
  });

  it('sets limits only after bringing the FSM to UnInited', async () => {
    const f = fakePlc();
    await f.send({ cmd: 'GA_EV', ev: 2 }); await f.send({ cmd: 'GA_EV', ev: 4 }); await f.send({ cmd: 'GA_EV', ev: 7 });
    expect(f.state()).toBe('Ready');
    const r = await applyLimits(f.send, A_AXIS, { v: 100, a: 1000, d: 1000, j: 10000 });
    expect(r.v).toBe(100);
    expect(f.state()).toBe('UnInited');
  });
});

describe('A test', () => {
  it('holds the arm where it is and swings only A, back to 0', async () => {
    const f = fakePlc();
    const r = await runATest(f.send, { amplitude: 180, cycles: 3 });
    expect(f.g1s[0]).toMatchObject({ X: 1, Y: 2, Z: 12, A: 0 });
    const swings = f.g1s.slice(1);
    expect(swings.map((g) => g.A)).toEqual([180, 0, 180, 0, 180, 0]);
    expect(swings.every((g) => g.X === undefined && g.Y === undefined && g.Z === undefined)).toBe(true);
    expect(r.moves).toBe(6);
    expect(r.peakPct.v).toBeCloseTo(100);
  });

  it('refuses when the arm position is not valid', async () => {
    const f = fakePlc({ armOk: false });
    await expect(runATest(f.send, { amplitude: 90, cycles: 1 })).rejects.toThrow(/arm position/);
    expect(f.g1s.length).toBe(0);
  });

  it('ladder stops at the first lost step and restores the downloaded limits', async () => {
    const f = fakePlc();
    const seen: number[] = [];
    const steps = await runALadder(f.send, {
      base: { v: 2880, a: 43200, d: 43200, j: 864000 }, factors: [1, 1.5, 2, 3], mode: 'time',
      test: { amplitude: 90, cycles: 1 },
      confirm: async (s) => { seen.push(s.result!.limits.v); return s.factor < 2; },
    });
    expect(steps.map((s) => s.verdict)).toEqual(['ok', 'ok', 'lost', undefined]);
    expect(seen).toEqual([288, 432, 576]);                 // v in u: 288 x f
    expect(f.lim.lv).toBe(288);                             // restored
    expect(f.state()).toBe('UnInited');
  });
});
