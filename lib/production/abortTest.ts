// Redirect test: head from the inspection station toward the tape, then
// after `delayMs` turn to the part-NG bin, either by aborting the running
// move (G1 abort, with its dynamics scaled by `scale`) or by blending into
// the bin move. Measures how long the redirect takes; the PLC's servo
// peak monitor (GVL.MotionPeak*) measures what it costs the joints.

import { cmd } from '../protocol';
import { GEOMETRY, feedConfig } from './params';
import type { Machine } from './machine';

export type AbortTestOptions = {
  /** 'stepped': the approach goes out as short segments, the next one
   *  only when the current one is half done; the redirect is a plain
   *  (blended) move queued behind the segment in progress. */
  mode: 'abort' | 'blend' | 'stepped';
  /** Segments of the approach in 'stepped' mode. */
  steps?: number;
  /** Time after the move toward the tape is sent until the redirect. */
  delayMs: number;
  /** ACC/DEA/JERK of the redirect move relative to the normal ones. */
  scale: number;
  feed: number;
};

export async function runAbortTest(m: Machine, o: AbortTestOptions) {
  const z = GEOMETRY.SAFE_Z;
  const start = { X: GEOMETRY.INSP_LOCATION.X, Y: GEOMETRY.INSP_LOCATION.Y };
  const toward = { X: GEOMETRY.SLOT_LOCATION.X + GEOMETRY.SLOT_PITCH_MM, Y: GEOMETRY.SLOT_LOCATION.Y };
  const f = 0.8;
  const target = { X: start.X + (toward.X - start.X) * f, Y: start.Y + (toward.Y - start.Y) * f };
  const bin = GEOMETRY.TOSS_PART_NG;
  const dyn = feedConfig(o.feed);

  await m.send(cmd.G1({ ...start, Z: z, ...dyn, Cor: 45 }));
  await m.send(cmd.WaitForMotionStop({ timeout_ms: 10000 }), true, 12000);

  const t0 = Date.now();
  if (o.mode === 'stepped') {
    const n = Math.max(1, o.steps ?? 4);
    const at = (k: number) => ({ X: start.X + (target.X - start.X) * k / n, Y: start.Y + (target.Y - start.Y) * k / n });
    await m.send(cmd.G1({ ...at(1), Z: z }));
    for (let k = 2; k <= n && Date.now() - t0 < o.delayMs; k++) {
      // next segment once the current one is half done (or the verdict came)
      await m.send(cmd.WaitForTriggerMotionProgress({ motion_id_offset: 0, motion_progress: 0.5 }));
      if (Date.now() - t0 >= o.delayMs) break;
      await m.send(cmd.G1({ ...at(k), Z: z }));
    }
    const wait = o.delayMs - (Date.now() - t0);
    if (wait > 0) await m.delay(wait);
  } else {
    await m.send(cmd.G1({ ...target, Z: z }));
    await m.delay(o.delayMs);
  }
  const tRedirect = Date.now();
  if (o.mode === 'abort') {
    await m.send(cmd.G1({
      X: bin.X, Y: bin.Y, Z: z, abort: true,
      ACC: dyn.ACC * o.scale, DEA: dyn.DEA * o.scale, JERK: dyn.JERK * o.scale,
    }));
  } else {
    await m.send(cmd.G1({ X: bin.X, Y: bin.Y, Z: z }));
  }
  await m.send(cmd.WaitForMotionStop({ timeout_ms: 10000 }), true, 12000);
  const tDone = Date.now();
  // Dynamics are modal on the PLC: put the normal ones back.
  await m.send(cmd.G1({ X: bin.X, Y: bin.Y, Z: z, ...dyn }));
  return { mode: o.mode, delayMs: o.delayMs, scale: o.scale, redirectMs: tDone - tRedirect, totalMs: tDone - t0 };
}
