// Motor limit test: the axes' settings as the PLC has them, and a test that
// turns an axis back and forth at chosen dynamic limits (A first).
//
// The PLC side: SYS AXIS_INFO (one axis: state, positions, limits in force
// and as downloaded, peaks since reset, increments per unit, the arm's TCP)
// and SYS SET_AXIS_LIMITS (run-time limits, only in UnInited: the group's
// planner takes an axis' limits when the group is enabled; changing them
// under an enabled group stops it with GroupErrorStop).
//
// No React here: MotorTestPage drives it, the unit tests fake `send`.

import { cmd } from './protocol';

/** PLC reply of SYS AXIS_INFO (axis units). */
export interface AxisInfo {
  axis: number;
  virt: boolean;
  st: number;           // SMC_AXIS_STATE
  err_flag: boolean;
  pos: number; act: number; vel: number;
  lv: number; la: number; ld: number; lj: number;   // limits in force
  cv: number; ca: number; cd: number; cj: number;   // as downloaded
  sf: number;           // increments per axis unit
  pv: number; pa: number; pj: number;               // peaks since reset
  arm_ok?: boolean; arm_x?: number; arm_y?: number; arm_z?: number;
}

export interface MotorMeta {
  axis: number;         // index in AXIS_INFO / the PLC's PeakAxis
  name: string;         // PLC axis name
  label: string;
  unit: string;         // axis unit
  /** What the operator reads: `show.per` of it per axis unit. */
  show: { unit: string; per: number };
  /** Encoder / step increments per motor turn (for rpm). */
  incsPerTurn: number;
  /** The page can run the back-and-forth test on it. */
  testable: boolean;
}

export const MOTORS: MotorMeta[] = [
  { axis: 0, name: 'EAxis0', label: 'Delta arm 1 (ASDA-B3, 31:1)', unit: 'deg', show: { unit: 'deg', per: 1 }, incsPerTurn: 2 ** 24, testable: false },
  { axis: 1, name: 'EAxis1', label: 'Delta arm 2 (ASDA-B3, 31:1)', unit: 'deg', show: { unit: 'deg', per: 1 }, incsPerTurn: 2 ** 24, testable: false },
  { axis: 2, name: 'EAxis2', label: 'Delta arm 3 (ASDA-B3, 31:1)', unit: 'deg', show: { unit: 'deg', per: 1 }, incsPerTurn: 2 ** 24, testable: false },
  // Kin_CAxis with the /10 wrap: 1 u = 10 deg of the real A (36 u per turn).
  { axis: 3, name: 'SM_Drive_GenericDSP402', label: 'A rotation (QEC axis 2, open-loop stepper)', unit: 'u', show: { unit: 'deg', per: 10 }, incsPerTurn: 6400, testable: true },
  { axis: 4, name: 'EAXIS_A', label: 'QEC axis 1 (nothing wired)', unit: 'u', show: { unit: 'deg', per: 10 }, incsPerTurn: 6400, testable: false },
  { axis: 5, name: 'reelpullmotor', label: 'Reel (CL3-E57H)', unit: 'mm', show: { unit: 'mm', per: 1 }, incsPerTurn: 51200, testable: false },
];

export const A_AXIS = 3;

/** Motor turns per axis unit, from the PLC's scale factor. */
export const turnsPerUnit = (m: MotorMeta, sf: number) => Math.abs(sf) / m.incsPerTurn;

/** Limits in display units (A: degrees) -> axis units, and back. */
export interface Limits { v: number; a: number; d: number; j: number }
export const toAxisUnits = (m: MotorMeta, l: Limits): Limits =>
  ({ v: l.v / m.show.per, a: l.a / m.show.per, d: l.d / m.show.per, j: l.j / m.show.per });
export const toShowUnits = (m: MotorMeta, l: Limits): Limits =>
  ({ v: l.v * m.show.per, a: l.a * m.show.per, d: l.d * m.show.per, j: l.j * m.show.per });

/** Scale limits by f. 'time': the same motion f times faster (v x f,
 *  a x f^2, j x f^3). 'all': every limit x f. */
export function scaleLimits(l: Limits, f: number, mode: 'time' | 'all'): Limits {
  if (mode === 'all') return { v: l.v * f, a: l.a * f, d: l.d * f, j: l.j * f };
  return { v: l.v * f, a: l.a * f * f, d: l.d * f * f, j: l.j * f * f * f };
}

/** PLC command, awaiting its reply; rejects on NAK / timeout. */
export type Send = (pkt: unknown, timeoutMs?: number) => Promise<any>;

const EV = { POWER_ON: 2, GROUP_ENABLE: 4, HOME_FORCE_SKIP: 7, RESET: 8 };
const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function readAxis(send: Send, axis: number, resetPeaks = false): Promise<AxisInfo> {
  return await send(cmd.AxisInfo(axis, resetPeaks)) as AxisInfo;
}

export async function fsmState(send: Send): Promise<string> {
  return String((await send({ type: 'SYS', cmd: 'GA_EV', ev: 0 }))?.st_str ?? '');
}

async function event(send: Send, ev: number) {
  try { await send({ type: 'SYS', cmd: 'GA_EV', ev }); } catch { /* a refused step shows in the state */ }
}

/** Group disabled (reset), so limits can be set. */
export async function toUnInited(send: Send, timeoutMs = 20000): Promise<void> {
  const end = Date.now() + timeoutMs;
  for (;;) {
    const st = await fsmState(send);
    if (st === 'UnInited') return;
    if (Date.now() > end) throw new Error(`FSM did not reach UnInited (${st})`);
    if (st === 'Ready' || st === 'Error' || st === 'Powered' || st === 'GroupEnabled') await event(send, EV.RESET);
    await delay(300);
  }
}

/** UnInited -> Powered -> GroupEnabled -> Ready (homing skipped). */
export async function toReady(send: Send, timeoutMs = 40000): Promise<void> {
  const end = Date.now() + timeoutMs;
  for (;;) {
    const st = await fsmState(send);
    if (st === 'Ready') return;
    if (Date.now() > end) throw new Error(`FSM did not reach Ready (${st})`);
    const ev = ({ UnInited: EV.POWER_ON, Powered: EV.GROUP_ENABLE, GroupEnabled: EV.HOME_FORCE_SKIP, Error: EV.RESET } as Record<string, number>)[st];
    if (ev !== undefined) await event(send, ev);
    await delay(300);
  }
}

/** Set an axis' limits (axis units) or restore the downloaded ones; the
 *  FSM is brought to UnInited for it and left there. */
export async function applyLimits(send: Send, axis: number, lim: Limits | 'restore'): Promise<Limits> {
  await toUnInited(send);
  const r = await send(cmd.SetAxisLimits(axis, lim));
  return { v: r.lv, a: r.la, d: r.ld, j: r.lj };
}

export interface TestOpts {
  /** Swing of each move (display units: degrees for A). */
  amplitude: number;
  /** Back-and-forth pairs. */
  cycles: number;
}

export interface TestResult {
  limits: Limits;        // axis units, in force during the test
  moves: number;
  elapsedS: number;
  perMoveS: number;
  peak: { v: number; a: number; j: number };      // axis units
  peakPct: { v: number; a: number; j: number };   // of the limit
}

/**
 * Turn A back and forth `cycles` times by `amplitude` degrees at the limits
 * in force (set them first with applyLimits), from and back to A = 0. Only
 * A moves: the first G1 goes to where the arm already is. Every G1 is
 * sent at once; the PLC takes them as its queue frees.
 */
export async function runATest(send: Send, opts: TestOpts, log: (s: string) => void = () => {}): Promise<TestResult> {
  if (!(opts.amplitude > 0) || !(opts.cycles >= 1)) throw new Error('amplitude > 0 and cycles >= 1');
  await toReady(send);
  const info0 = await readAxis(send, A_AXIS);
  if (!info0.arm_ok || info0.arm_x === undefined) throw new Error('arm position not valid: A test refused (it would move the arm)');
  // Slow into A = 0 where the arm is (no XYZ travel). F/ACC are huge on
  // purpose below: A's own limits then set the pace of every move.
  await send(cmd.G1({ X: info0.arm_x, Y: info0.arm_y, Z: info0.arm_z, A: 0, F: 50, ACC: 2000, DEA: 2000, JERK: 20000, Cor: 0 }));
  await send(cmd.WaitForMotionStop({ timeout_ms: 30000 }), 35000);
  await readAxis(send, A_AXIS, true);
  await delay(50);                                  // the peak reset lands next scan
  const fast = { F: 100000, ACC: 10000000, DEA: 10000000, JERK: 1000000000, Cor: 0 };
  const t0 = Date.now();
  for (let k = 0; k < opts.cycles; k++) {
    await send(cmd.G1({ A: opts.amplitude, ...fast }), 60000);
    await send(cmd.G1({ A: 0, ...fast }), 60000);
  }
  await send(cmd.WaitForMotionStop({ timeout_ms: 120000 }), 125000);
  const elapsedS = (Date.now() - t0) / 1000;
  const info = await readAxis(send, A_AXIS);
  const moves = 2 * opts.cycles;
  const pct = (x: number, lim: number) => (lim > 0 ? (100 * x) / lim : 0);
  const res: TestResult = {
    limits: { v: info.lv, a: info.la, d: info.ld, j: info.lj },
    moves,
    elapsedS,
    perMoveS: elapsedS / moves,
    peak: { v: info.pv, a: info.pa, j: info.pj },
    peakPct: { v: pct(info.pv, info.lv), a: pct(info.pa, info.la), j: pct(info.pj, info.lj) },
  };
  log(`A test: ${moves} moves of ${opts.amplitude} deg in ${elapsedS.toFixed(2)} s (${res.perMoveS.toFixed(3)} s each), `
    + `peaks v ${res.peakPct.v.toFixed(0)}% a ${res.peakPct.a.toFixed(0)}% of the limits`);
  return res;
}

export interface LadderStep { factor: number; limitsShow: Limits; result?: TestResult; verdict?: 'ok' | 'lost' | 'error'; error?: string }

/**
 * Raise A's limits step by step from `base` (display units): each step
 * sets the limits, runs the test, then asks `confirm` whether A came back
 * to its mark (the operator looks; false = steps lost). Stops at the
 * first loss or error. The downloaded limits are restored at the end
 * unless keepLast.
 */
export async function runALadder(send: Send, o: {
  base: Limits; factors: number[]; mode: 'time' | 'all'; test: TestOpts; keepLast?: boolean;
  confirm: (step: LadderStep) => Promise<boolean>; onStep?: (steps: LadderStep[]) => void; log?: (s: string) => void;
}): Promise<LadderStep[]> {
  const meta = MOTORS[A_AXIS];
  const steps: LadderStep[] = o.factors.map((f) => ({ factor: f, limitsShow: scaleLimits(o.base, f, o.mode) }));
  try {
    for (const s of steps) {
      o.log?.(`step x${s.factor}: v ${s.limitsShow.v.toFixed(0)} a ${s.limitsShow.a.toFixed(0)} j ${s.limitsShow.j.toFixed(0)} ${meta.show.unit}`);
      try {
        await applyLimits(send, A_AXIS, toAxisUnits(meta, s.limitsShow));
        s.result = await runATest(send, o.test, o.log);
      } catch (e: any) {
        s.verdict = 'error';
        s.error = String(e?.message ?? e);
        o.onStep?.(steps);
        break;
      }
      o.onStep?.(steps);
      s.verdict = (await o.confirm(s)) ? 'ok' : 'lost';
      o.onStep?.(steps);
      if (s.verdict === 'lost') break;
    }
  } finally {
    if (!o.keepLast) {
      try { await applyLimits(send, A_AXIS, 'restore'); o.log?.('downloaded limits restored'); }
      catch (e: any) { o.log?.(`restore failed: ${e?.message ?? e}`); }
    }
  }
  return steps;
}
