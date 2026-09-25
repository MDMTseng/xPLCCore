// Tape-sensor monitoring: DI events and polls merged per pin, and the rules
// the input watchdog applies each tick.
import { describe, it, expect } from 'vitest';
import { IO_PINS } from './io';
import { WATCHDOG } from './params';
import { InputMonitor, TAPE_SENSOR_PINS, checkTapeSensors, type DiEvent } from './inputs';

const I = IO_PINS.I;
const NORMAL = (1 << I.PackedReelNoProtrusion) | (1 << I.ReelPressRollerInPlace);   // 0x0C00
const fc0 = new Array(16).fill(0);

function ev(pin: number, state: boolean, flips = 1): DiEvent {
  return { kind: 'event', name: 'DI', pin, state, flips, high_ms: 0, max_high_ms: 0, max_low_ms: 0, t_first: 0, reel_pos: 0, t: 0 };
}

function normalMonitor() {
  const m = new InputMonitor(TAPE_SENSOR_PINS);
  m.onPoll(NORMAL, fc0);
  return m;
}

describe('InputMonitor', () => {
  it('watches the four tape sensors', () => {
    expect(new InputMonitor(TAPE_SENSOR_PINS).mask).toBe(0x0F00);
  });

  it('knows the levels only after a poll or an event for every pin', () => {
    const m = new InputMonitor(TAPE_SENSOR_PINS);
    expect(m.known).toBe(false);
    m.onPoll(NORMAL, fc0);
    expect(m.known).toBe(true);
    expect(m.bits).toBe(NORMAL);
  });

  it('keeps a glitch that is already over when the rules look (event summary)', () => {
    const m = normalMonitor();
    // the protrusion input dropped for 1 ms and came back: one summarising event
    m.onEvent(ev(I.PackedReelNoProtrusion, true, 2));
    expect(m.take(I.PackedReelNoProtrusion)).toEqual({ level: true, changed: true });
    expect(m.take(I.PackedReelNoProtrusion)).toEqual({ level: true, changed: false });
  });

  it('catches a change whose event was lost from the poll counters', () => {
    const m = normalMonitor();
    const fc = [...fc0];
    fc[I.ReelTapeHTension] = 2;
    m.onPoll(NORMAL, fc);
    expect(m.take(I.ReelTapeHTension).changed).toBe(true);
  });

  it('ignores events for pins it does not watch', () => {
    const m = normalMonitor();
    m.onEvent(ev(3, true));
    expect(m.bits).toBe(NORMAL);
  });
});

describe('checkTapeSensors', () => {
  it('is quiet with all inputs normal', () => {
    expect(checkTapeSensors(normalMonitor(), 0)).toEqual({ errors: [], feedPulse: false, lackingTicks: 0 });
  });

  it('flags a protrusion glitch, a tension spike, a lifted press roller', () => {
    const m = normalMonitor();
    m.onEvent(ev(I.PackedReelNoProtrusion, true, 2));
    m.onEvent(ev(I.ReelTapeHTension, false, 2));
    m.onEvent(ev(I.ReelPressRollerInPlace, false));
    expect(checkTapeSensors(m, 0).errors).toEqual(['凸料感應', '冷封氣缸沒壓到', '上蓋帶張力過強']);
    // the glitches are over and reported; the press roller is still up
    expect(checkTapeSensors(m, 0).errors).toEqual(['冷封氣缸沒壓到']);
  });

  it('pulses the reel-wheel feed every other tick while the tape is lacking, then errs', () => {
    const m = normalMonitor();
    m.onEvent(ev(I.ReelLacking, true));
    let ticks = 0;
    const pulses: boolean[] = [];
    let errors: string[] = [];
    for (let k = 0; k <= WATCHDOG.REEL_LACKING_LIMIT; k++) {
      const v = checkTapeSensors(m, ticks);
      ticks = v.lackingTicks;
      pulses.push(v.feedPulse);
      errors = v.errors;
    }
    expect(pulses.slice(0, 4)).toEqual([true, false, true, false]);
    expect(errors).toEqual(['載帶缺料']);
    m.onEvent(ev(I.ReelLacking, false));
    expect(checkTapeSensors(m, ticks)).toEqual({ errors: [], feedPulse: false, lackingTicks: 0 });
  });
});
