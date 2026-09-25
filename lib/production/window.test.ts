import { describe, it, expect } from 'vitest';
import { createSendWindow } from './window';

/** A fake PLC: replies only when told to. */
function fakePlc() {
  const sent: number[] = [];
  const pending: { n: number; resolve: () => void; reject: (e: Error) => void }[] = [];
  const send = (pkt: unknown) => new Promise<unknown>((resolve, reject) => {
    sent.push(pkt as number);
    pending.push({ n: pkt as number, resolve: () => resolve(undefined), reject });
  });
  const replyOldest = () => pending.shift()!.resolve();
  return { send, sent, pending, replyOldest };
}
const tick = () => new Promise((r) => setTimeout(r, 0));

describe('createSendWindow', () => {
  it('sends up to max without waiting, then waits for the oldest reply', async () => {
    const plc = fakePlc();
    const w = createSendWindow(plc.send, 3, () => {});
    for (let k = 0; k < 5; k++) void w.queue(k);
    await tick();
    expect(plc.sent).toEqual([0, 1, 2]);
    plc.replyOldest();
    await tick(); await tick();
    expect(plc.sent).toEqual([0, 1, 2, 3]);
    plc.replyOldest(); plc.replyOldest();
    await tick(); await tick();
    expect(plc.sent).toEqual([0, 1, 2, 3, 4]);
  });

  it('keeps call order and reports failed replies', async () => {
    const plc = fakePlc();
    const errors: unknown[] = [];
    const w = createSendWindow(plc.send, 8, (pkt) => errors.push(pkt));
    await w.queue(1); await w.queue(2);
    plc.pending[0].reject(new Error('nak'));
    plc.replyOldest();
    await tick();
    expect(plc.sent).toEqual([1, 2]);
    expect(errors).toEqual([1]);
  });

  it('drain waits for every outstanding reply', async () => {
    const plc = fakePlc();
    const w = createSendWindow(plc.send, 8, () => {});
    await w.queue(1); await w.queue(2);
    let drained = false;
    void w.drain().then(() => { drained = true; });
    await tick();
    expect(drained).toBe(false);
    plc.replyOldest(); plc.replyOldest();
    await tick(); await tick(); await tick();
    expect(drained).toBe(true);
  });
});
