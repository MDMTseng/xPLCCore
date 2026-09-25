// Path feed test: the host streams many short G1 moves and we measure how
// long the arm takes to run them. When the host cannot keep the PLC's
// motion buffer fed, the arm stops between moves (IDLE events in the PLC
// event log) and the path takes longer than its moves.

import { cmd } from '../protocol';
import { feedConfig } from './params';
import type { Machine } from './machine';

export type PathTestOptions = {
  /** Moves around the circle. */
  n: number;
  radius: number;
  center: { X: number; Y: number; Z: number };
  /** Feed (mm/s) for the path moves. */
  feed: number;
  /** 'await': a round trip per move (today's G1 chains); 'queue': the
   *  send window (Machine.queue). */
  mode: 'await' | 'queue';
  /** Corner blending distance (mm) sent with the first move ('Cor', sticky
   *  on the PLC): < 1 = Buffered (stop at every point), >= 1 = BlendingNext. */
  cor?: number;
  /** JERK = feed * jerkRatio (default: feedConfig's 400). ACC stays feed * 100. */
  jerkRatio?: number;
};

export type PathTestResult = {
  mode: string;
  n: number;
  cor?: number;
  /** Send of the first move to the arm standing still after the last. */
  ms: number;
  /** Host time spent sending (until the last move was handed over). */
  sendMs: number;
  pathMm: number;
  segmentMm: number;
};

export async function runPathTest(m: Machine, o: PathTestOptions): Promise<PathTestResult> {
  const p = (k: number) => ({
    X: o.center.X + o.radius * Math.cos((2 * Math.PI * k) / o.n),
    Y: o.center.Y + o.radius * Math.sin((2 * Math.PI * k) / o.n),
  });
  // start point, at the path feed, and wait until the arm is there
  const dyn = { ...feedConfig(o.feed), ...(o.jerkRatio ? { JERK: o.feed * o.jerkRatio } : {}) };
  await m.send(cmd.G1({ ...p(0), Z: o.center.Z, ...dyn, ...(o.cor !== undefined ? { Cor: o.cor } : {}) }));
  await m.send(cmd.WaitForMotionStop({ timeout_ms: 10000 }), true, 12000);

  const t0 = Date.now();
  for (let k = 1; k <= o.n; k++) {
    const g1 = cmd.G1(p(k));
    if (o.mode === 'await') await m.send(g1);
    else await m.queue(g1);
  }
  const sendMs = Date.now() - t0;
  // FIFO on the PLC: this registers after the last move was accepted, and
  // acks when the arm stands still.
  await m.send(cmd.WaitForMotionStop({ timeout_ms: 60000 }), true, 65000);
  const ms = Date.now() - t0;
  const segmentMm = 2 * o.radius * Math.sin(Math.PI / o.n);
  return { mode: o.mode, n: o.n, cor: o.cor, ms, sendMs, pathMm: segmentMm * o.n, segmentMm };
}
