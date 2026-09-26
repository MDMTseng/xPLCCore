// Jog test (tools/sim/run_virtual.py --jog-test): replay a synthetic drag
// -- a circle traced at `hz` pointer events per second -- through the jog
// streamer, or through the old one-G1-per-event pad for comparison, and
// measure how the arm followed. The PLC's servo peak monitor
// (GVL.MotionPeak*) says what it cost the joints.

import { cmd } from './protocol';
import { JogStreamer } from './jog';
import { feedConfig } from './production/params';

type Send = (pkt: any) => Promise<any>;

export type JogTestOptions = { mode: 'stream' | 'legacy'; hz: number; seconds: number; radius: number };

export async function runJogTest(send: Send, o: JogTestOptions) {
  const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));
  // the dynamics a production run leaves behind (the old pad inherited them)
  await send(cmd.G1({ X: 0, Y: 0, Z: 12, ...feedConfig(1000) }));
  await send(cmd.WaitForMotionStop({ timeout_ms: 10000 }));

  const n = Math.round(o.hz * o.seconds);
  const step = (k: number) => {
    const a0 = 2 * Math.PI * k / n, a1 = 2 * Math.PI * (k + 1) / n;
    return { X: o.radius * (Math.cos(a1) - Math.cos(a0)), Y: o.radius * (Math.sin(a1) - Math.sin(a0)) };
  };
  let g1 = 0, dropped = 0;
  const counting: Send = (pkt) => { if (pkt.cmd === 'G1') g1++; return send(pkt); };
  const t0 = Date.now();
  let target = { X: 0, Y: 0 };

  if (o.mode === 'stream') {
    const j = new JogStreamer(counting);
    await j.press();
    for (let k = 0; k < n; k++) {
      const d = step(k);
      target = { X: target.X + d.X, Y: target.Y + d.Y };
      j.move(d);
      await delay(1000 / o.hz);
    }
    j.release();
    // wait until the streamer has sent its last step
    for (let w = 0; w < 400 && !j.idle; w++) await delay(25);
    j.stop();
  } else {
    // the old pad: one G1 per event, events during a reply dropped
    await send(cmd.WaitForMotionStop());
    const base = await send(cmd.ReadLatestCmdLocation());
    const pos = { X: Number(base.X), Y: Number(base.Y), Z: Number(base.Z) };
    let busy = false;
    const pending: Promise<unknown>[] = [];
    for (let k = 0; k < n; k++) {
      const d = step(k);
      target = { X: target.X + d.X, Y: target.Y + d.Y };
      if (busy) { dropped++; } else {
        pos.X += d.X; pos.Y += d.Y;
        busy = true;
        pending.push(counting(cmd.G1({ ...pos })).finally(() => { busy = false; }));
      }
      await delay(1000 / o.hz);
    }
    await Promise.all(pending);
  }
  const tRelease = Date.now();
  await send(cmd.WaitForMotionStop({ timeout_ms: 20000 }));
  const tStop = Date.now();
  const at = await send(cmd.ReadLatestCmdLocation());
  return {
    mode: o.mode, events: n, g1, dropped,
    dragMs: tRelease - t0,
    settleMs: tStop - tRelease,                       // arm still moving after the last event
    endErrorMm: Math.hypot(Number(at.X) - target.X, Number(at.Y) - target.Y),
  };
}
