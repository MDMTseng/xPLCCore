// The tape sensors during a run: what each input is doing, and the rules.
//
// The PLC samples the inputs every 1 ms scan and pushes a DI event when a
// watched one changes (SYS DI_WATCH): the first change at once, the rest
// summarised once per throttle period, so even a 1 ms glitch is seen. A
// slow poll of the level and flip counters (GET_DIGITAL_INPUT_FLIP_COUNT)
// backs the events up: an event lost to a full reply ring is still
// caught by the next poll. The rules only need ~2 s to react; they run
// on a timer over what `InputMonitor` gathered since they last looked.

import { WATCHDOG } from './params';
import { IO_PINS } from './io';

/** A DI change event from the PLC (UpdateDiWatch), summarising the
 *  changes of one pin since its previous event. */
export type DiEvent = {
  kind: 'event';
  name: 'DI';
  pin: number;
  state: boolean;
  flips: number;
  high_ms: number;
  max_high_ms: number;
  max_low_ms: number;
  t_first: number;
  reel_pos: number;
  t: number;
  seq?: number;
};

export type PinView = {
  /** Level now (last event or poll). */
  level: boolean;
  /** Changed since the rules last looked (any edge, however short). */
  changed: boolean;
};

export class InputMonitor {
  private level = new Map<number, boolean>();
  private changed = new Map<number, boolean>();
  private prevFc: number[] | undefined;
  private raw = 0;

  constructor(private readonly pins: number[]) {}

  /** Watched pins as a DI_WATCH mask. */
  get mask(): number {
    return this.pins.reduce((m, p) => m | (1 << p), 0);
  }

  onEvent(e: DiEvent) {
    if (!this.pins.includes(e.pin)) return;
    this.level.set(e.pin, !!e.state);
    this.raw = e.state ? this.raw | (1 << e.pin) : this.raw & ~(1 << e.pin);
    if (e.flips > 0) this.changed.set(e.pin, true);
  }

  /** A poll: the levels of all pins and their cumulative flip counters. */
  onPoll(raw: number, fc: number[]) {
    this.raw = raw;
    for (const p of this.pins) {
      this.level.set(p, ((raw >> p) & 1) === 1);
      if (this.prevFc && (fc[p] ?? 0) > (this.prevFc[p] ?? 0)) this.changed.set(p, true);
    }
    this.prevFc = [...fc];
  }

  /** Has there been a poll or an event for every watched pin? */
  get known(): boolean {
    return this.pins.every((p) => this.level.has(p));
  }

  /** The input bits as last seen (for the page's input display). */
  get bits(): number {
    return this.raw;
  }

  /** What `pin` did since the last take, and start over. */
  take(pin: number): PinView {
    const v = { level: this.level.get(pin) ?? false, changed: this.changed.get(pin) ?? false };
    this.changed.set(pin, false);
    return v;
  }
}

export const TAPE_SENSOR_PINS = [
  IO_PINS.I.ReelLacking,
  IO_PINS.I.ReelTapeHTension,
  IO_PINS.I.ReelPressRollerInPlace,
  IO_PINS.I.PackedReelNoProtrusion,
];

export type TapeSensorVerdict = {
  /** Operator messages; empty = all fine. */
  errors: string[];
  /** Pulse the reel-wheel feed now (the carrier tape is lacking). */
  feedPulse: boolean;
  /** Ticks in a row with the carrier tape lacking. */
  lackingTicks: number;
};

/**
 * The tape-sensor rules, once per tick (WATCHDOG.POLL_MS):
 *  - carrier tape lacking: pulse the reel-wheel feed every other tick; an
 *    error after WATCHDOG.REEL_LACKING_LIMIT ticks;
 *  - packed reel protrusion: must stay high the whole tick;
 *  - cold-seal press roller: must be in place;
 *  - cover-tape tension: too high now, or at any moment of the tick.
 */
export function checkTapeSensors(m: InputMonitor, lackingTicks: number): TapeSensorVerdict {
  const lacking = m.take(IO_PINS.I.ReelLacking);
  const tension = m.take(IO_PINS.I.ReelTapeHTension);
  const roller = m.take(IO_PINS.I.ReelPressRollerInPlace);
  const protrusion = m.take(IO_PINS.I.PackedReelNoProtrusion);

  let feedPulse = false;
  if (lacking.level) {
    feedPulse = (lackingTicks & 1) === 0;
    lackingTicks++;
  } else {
    lackingTicks = 0;
  }

  const errors: string[] = [];
  if (!protrusion.level || protrusion.changed) errors.push('凸料感應');
  if (!roller.level) errors.push('冷封氣缸沒壓到');
  if (tension.level || tension.changed) errors.push('上蓋帶張力過強');
  if (lackingTicks > WATCHDOG.REEL_LACKING_LIMIT) errors.push('載帶缺料');
  return { errors, feedPulse, lackingTicks };
}
