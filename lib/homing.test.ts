import { describe, it, expect } from 'vitest';
import { homingEvent, deltaVirtual } from './homing';
import { Event } from './protocol';

describe('homingEvent', () => {
  it('skips homing when the PLC reports the delta trio virtual', async () => {
    const r = await homingEvent(async () => ({ st_str: 'GroupEnabled', axes_sim_mask: 0x0f }));
    expect(r).toEqual({ ev: Event.HOME_GO_FORCE_SKIP, skip: true });
  });

  it('homes when any delta axis is real', async () => {
    expect((await homingEvent(async () => ({ axes_sim_mask: 0x08 }))).ev).toBe(Event.HOME_GO);
    expect((await homingEvent(async () => ({ axes_sim_mask: 0x03 }))).ev).toBe(Event.HOME_GO);
  });

  it('homes when the state cannot be read', async () => {
    expect((await homingEvent(async () => { throw new Error('link'); })).ev).toBe(Event.HOME_GO);
    expect((await homingEvent(async () => ({}))).ev).toBe(Event.HOME_GO);
  });

  it('deltaVirtual reads bits 0-2 only', () => {
    expect(deltaVirtual(7)).toBe(true);
    expect(deltaVirtual(15)).toBe(true);
    expect(deltaVirtual(6)).toBe(false);
    expect(deltaVirtual(undefined)).toBe(false);
  });
});
