// Jogging the arm from a drag pad: pointer deltas in, a steady stream of
// short blended G1 moves out.
//
// It used to send one G1 per pointer event and drop every event that came
// while that G1 was being answered: the arm lagged, then jumped, and each
// tiny move accelerated and stopped on its own (with whatever feed the
// last command had left) -- choppy. Now:
//  - every delta goes into the target (none dropped);
//  - a timer sends the latest target every JOG.TICK_MS, at most one G1 in
//    flight, each step at most JOG.F * TICK_MS long: the arm follows about
//    a tick behind and the PLC queue never builds up;
//  - the steps use the jog dynamics and blend into each other (Cor).

import { cmd } from './protocol';
import { JOG } from './production/params';

export type XYZ = { X: number; Y: number; Z: number };

type Send = (pkt: any) => Promise<any>;

export class JogStreamer {
  private base: XYZ | undefined;          // commanded position at the press
  private off: XYZ = { X: 0, Y: 0, Z: 0 };
  private sent: XYZ | undefined;          // last position sent
  private active = false;
  private inFlight = false;
  private timer: ReturnType<typeof setInterval> | undefined;
  private epoch = 0;

  constructor(
    private readonly send: Send,
    private readonly onPos?: (p: XYZ) => void,
    private readonly onError?: (e: unknown) => void,
  ) {}

  /** Pointer down: start from where the arm was last commanded to be. */
  async press() {
    const epoch = ++this.epoch;
    this.active = true;
    this.off = { X: 0, Y: 0, Z: 0 };
    this.base = undefined;
    this.sent = undefined;
    this.start();
    try {
      await this.send(cmd.WaitForMotionStop());
      const loc = await this.send(cmd.ReadLatestCmdLocation());
      if (epoch !== this.epoch) return;          // a newer press took over
      this.base = { X: Number(loc?.X), Y: Number(loc?.Y), Z: Number(loc?.Z) };
      if ([this.base.X, this.base.Y, this.base.Z].some((v) => !Number.isFinite(v))) {
        throw new Error('no commanded location from the PLC');
      }
      this.sent = { ...this.base };
      this.onPos?.({ ...this.base });
    } catch (e) {
      this.fail(e);
    }
  }

  /** Pointer moved by `d` (machine units, already scaled). Never dropped:
   *  deltas before the start location is known are applied to it. */
  move(d: Partial<XYZ>) {
    if (!this.active) return;
    this.off.X += d.X ?? 0;
    this.off.Y += d.Y ?? 0;
    this.off.Z += d.Z ?? 0;
  }

  /** Pointer up: the arm still goes to where the pad was left. */
  release() {
    this.active = false;
  }

  /** Nothing left to send (released and arrived, stopped, or failed). */
  get idle(): boolean {
    return this.timer === undefined;
  }

  stop() {
    this.active = false;
    if (this.timer !== undefined) clearInterval(this.timer);
    this.timer = undefined;
  }

  /** One step toward the target; the timer calls it every JOG.TICK_MS. */
  async tick() {
    if (this.base === undefined || this.sent === undefined) return;
    const target = { X: this.base.X + this.off.X, Y: this.base.Y + this.off.Y, Z: this.base.Z + this.off.Z };
    const d = { X: target.X - this.sent.X, Y: target.Y - this.sent.Y, Z: target.Z - this.sent.Z };
    const dist = Math.hypot(d.X, d.Y, d.Z);
    if (dist < JOG.MIN_STEP_MM) {
      if (!this.active) this.stop();              // released and arrived
      return;
    }
    if (this.inFlight) return;                    // the target keeps; next tick
    const maxStep = JOG.F * JOG.TICK_MS / 1000;
    const k = dist > maxStep ? maxStep / dist : 1;
    const next = { X: this.sent.X + d.X * k, Y: this.sent.Y + d.Y * k, Z: this.sent.Z + d.Z * k };
    this.inFlight = true;
    try {
      await this.send(cmd.G1({ ...next, F: JOG.F, ACC: JOG.ACC, DEA: JOG.ACC, JERK: JOG.JERK, Cor: JOG.COR }));
      this.sent = next;
      this.onPos?.({ ...next });
    } catch (e) {
      this.fail(e);
    } finally {
      this.inFlight = false;
    }
  }

  private start() {
    if (this.timer === undefined) this.timer = setInterval(() => { void this.tick(); }, JOG.TICK_MS);
  }

  private fail(e: unknown) {
    this.stop();
    this.base = undefined;
    this.onError?.(e);
  }
}
