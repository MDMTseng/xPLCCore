// JogStreamer against a fake PLC with a slow G1 reply: no pointer delta is
// lost, steps are capped and blended, one G1 in flight, and the arm ends
// where the pad was released.
import { describe, it, expect, vi, afterEach } from 'vitest';
import { JogStreamer } from './jog';
import { JOG } from './production/params';

afterEach(() => { vi.useRealTimers(); });

function fakePlc(replyMs = 30) {
  const g1: any[] = [];
  let inFlight = 0;
  let maxInFlight = 0;
  const send = async (pkt: any) => {
    if (pkt.cmd === 'READ_LATEST_CMD_LOCATION') return { X: 10, Y: 20, Z: 5 };
    if (pkt.cmd === 'G1') {
      inFlight++;
      maxInFlight = Math.max(maxInFlight, inFlight);
      g1.push(pkt);
      await new Promise((r) => setTimeout(r, replyMs));
      inFlight--;
    }
    return { ack: true };
  };
  return { send, g1, maxInFlight: () => maxInFlight };
}

describe('JogStreamer', () => {
  it('follows every delta, one blended jog-speed step in flight at a time, and ends at the release point', async () => {
    vi.useFakeTimers();
    const plc = fakePlc(30);
    const j = new JogStreamer(plc.send);
    await j.press();
    // 200 pointer events of 0.05 mm each, 1 ms apart (faster than any reply)
    for (let k = 0; k < 200; k++) {
      j.move({ X: 0.05 });
      await vi.advanceTimersByTimeAsync(1);
    }
    j.release();
    await vi.advanceTimersByTimeAsync(5000);

    const last = plc.g1[plc.g1.length - 1];
    expect(last.X).toBeCloseTo(10 + 200 * 0.05, 6);     // nothing dropped
    expect(last.Y).toBeCloseTo(20, 6);
    expect(plc.maxInFlight()).toBe(1);
    const maxStep = JOG.F * JOG.TICK_MS / 1000;
    let prev = 10;
    for (const p of plc.g1) {
      expect(p.X - prev).toBeLessThanOrEqual(maxStep + 1e-9);
      prev = p.X;
      expect(p).toMatchObject({ F: JOG.F, ACC: JOG.ACC, JERK: JOG.JERK, Cor: JOG.COR });
    }
  });

  it('applies deltas made before the start location arrived', async () => {
    vi.useFakeTimers();
    const plc = fakePlc(1);
    const j = new JogStreamer(plc.send);
    const pressed = j.press();
    j.move({ Z: -1 });                                  // before the location reply
    await pressed;
    j.release();
    await vi.advanceTimersByTimeAsync(2000);
    expect(plc.g1[plc.g1.length - 1].Z).toBeCloseTo(4, 6);
  });

  it('stops and reports a NAK', async () => {
    vi.useFakeTimers();
    const errors: string[] = [];
    const j = new JogStreamer(async (pkt: any) => {
      if (pkt.cmd === 'READ_LATEST_CMD_LOCATION') return { X: 0, Y: 0, Z: 0 };
      if (pkt.cmd === 'G1') throw new Error('group_not_ready');
      return { ack: true };
    }, undefined, (e: any) => errors.push(e.message));
    await j.press();
    j.move({ X: 1 });
    await vi.advanceTimersByTimeAsync(500);
    expect(errors).toEqual(['group_not_ready']);
  });
});
