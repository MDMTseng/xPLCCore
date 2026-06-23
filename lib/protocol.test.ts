// Tests for the M4 reset_ms -> pin_op_seq converter.
//
// Migration story: the PLC used to expand `{pin, state, reset_ms}` into
// an internal 2-stage IO sequence (set, then auto-reset after reset_ms).
// 2026-06-23 the expansion moved to the TS side so the PLC handler has
// one canonical input (`pin_op_seq`). These tests pin the conversion
// shape so existing call sites that say `reset_ms: 50` keep producing
// the same wire packet the PLC used to build internally.

import { describe, it, expect } from 'vitest';
import { expandM4ResetMs } from './protocol';

describe('expandM4ResetMs', () => {
  it('passes through when no pin and no reset_ms', () => {
    const a = { motion_id_offset: 0 };
    expect(expandM4ResetMs(a)).toEqual(a);
  });

  it('builds a single-stage pin_op_seq when pin given without reset_ms', () => {
    const out = expandM4ResetMs({ pin: 0b1000, state: 0b1000 });
    expect(out).toEqual({ pin_op_seq: [0, 0b1000, 0b1000] });
  });

  it('builds a 2-stage pin_op_seq when reset_ms > 0', () => {
    const out = expandM4ResetMs({ pin: 0b1010, state: 0b1010, reset_ms: 50 });
    // stage 0: delay=0, mask=10, state=10 (turn bits on)
    // stage 1: delay=50, mask=10, state=(~10) & 10 = 0 (turn those bits off)
    expect(out).toEqual({ pin_op_seq: [0, 0b1010, 0b1010, 50, 0b1010, 0] });
  });

  it('reset stage masks the NOT so only pin bits flip', () => {
    // state has bits OUTSIDE the pin mask (host bug or composite mask
    // construction). The reset stage state must restrict to the mask.
    const out = expandM4ResetMs({ pin: 0b0100, state: 0b1111, reset_ms: 20 });
    // (~0b1111) & 0b0100 = 0b0000 -> turn the masked bit off
    expect(out).toEqual({ pin_op_seq: [0, 0b0100, 0b1111, 20, 0b0100, 0] });
  });

  it('appends to an existing pin_op_seq instead of replacing', () => {
    const out = expandM4ResetMs({
      pin_op_seq: [10, 0b1, 0b1],
      pin: 0b10,
      state: 0b10,
      reset_ms: 5,
    });
    expect(out.pin_op_seq).toEqual([10, 0b1, 0b1, 0, 0b10, 0b10, 5, 0b10, 0]);
  });

  it('strips pin/state/reset_ms from the returned shape', () => {
    const out = expandM4ResetMs({ pin: 1, state: 1, reset_ms: 5, ttl_ms: 100 });
    expect(out).not.toHaveProperty('pin');
    expect(out).not.toHaveProperty('state');
    expect(out).not.toHaveProperty('reset_ms');
    expect(out.ttl_ms).toBe(100);
  });

  it('drops reset_ms when pin is missing (had no effect pre-fix)', () => {
    const out = expandM4ResetMs({ reset_ms: 50, motion_id_offset: 0 });
    expect(out).toEqual({ motion_id_offset: 0 });
  });

  it('treats reset_ms=0 as no-reset', () => {
    const out = expandM4ResetMs({ pin: 1, state: 1, reset_ms: 0 });
    expect(out).toEqual({ pin_op_seq: [0, 1, 1] });
  });

  it('default state=0 when only pin given', () => {
    const out = expandM4ResetMs({ pin: 0b100 });
    expect(out).toEqual({ pin_op_seq: [0, 0b100, 0] });
  });
});
