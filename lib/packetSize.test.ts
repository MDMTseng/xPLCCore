// 2026-10-05 integration review: TAPE_CYCLE encoded to ~251 B against the
// PLC's 255 B host packet slot (GVL.MAX_SLOT_PAYLOAD); one more shot stage
// or a fractional value made every tape step NAK `packet_too_long`. These
// tests keep the real production packets inside the slot with margin.
import { describe, it, expect } from 'vitest';
import { decode } from '@msgpack/msgpack';
import { cmd, encodePacket, MAX_HOST_PACKET_BYTES } from './protocol';
import { GEOMETRY, TAPE } from './production/params';
import { topShotSequence } from './production/tape';

// What sendTcpMsgPack adds: the largest id / version it can stamp.
const stamp = (p: object) => ({ ...p, id: 2 ** 31 - 1, protocol_version: 1 });

const tapeArgs = (over: object = {}) => ({
  motion_id_offset: -1, motion_progress: 0,
  distance: 3 * TAPE.REEL_CELL_DISTANCE, ...TAPE.REEL_MOVE,
  x: GEOMETRY.SLOT_LOCATION.X + GEOMETRY.SLOT_PITCH_MM, y: GEOMETRY.SLOT_LOCATION.Y, z: GEOMETRY.SAFE_Z,
  radius: TAPE.TOP_CAM_CLEAR_MM, pin_op_seq: topShotSequence(), event_id: 2 ** 31 - 1,
  timeout_ms: Math.round((TAPE.REEL_STOP_TIMEOUT_MS + 3000) * 100),   // override 0.01
  ttl_ms: Math.round(TAPE.TOP_SHOT_TTL_MS * 100),
  cells: 31, kind: 'pack' as const,
  ...over,
});

describe('host packet size', () => {
  it('production TAPE_CYCLE fits with >= 24 B to spare', () => {
    const n = encodePacket(stamp(cmd.TapeCycle(tapeArgs()))).length;
    expect(n).toBeLessThanOrEqual(MAX_HOST_PACKET_BYTES - 24);
  });

  // The geometry comes from params.ts (distance = cells x 8, radius 40,
  // Z 12); a fractional trigger progress is the one value that varies. With
  // every value at its widest (fractional distance, radius and Z too, ids
  // past 2^31) it reaches ~258 B: sendTcpMsgPack refuses that before the PLC
  // does. Room for good needs bigger PLC slots or shorter keys (phase 2).
  it('TAPE_CYCLE with a fractional trigger fits down to override 0.01', () => {
    const n = encodePacket(stamp(cmd.TapeCycle(tapeArgs({ motion_progress: 0.37 })))).length;
    expect(n).toBeLessThanOrEqual(MAX_HOST_PACKET_BYTES - 8);
  });

  it('float32 keeps the trigger geometry to < 1e-5 mm', () => {
    const p: any = decode(encodePacket(stamp(cmd.TapeCycle(tapeArgs({ x: 49.731, y: -79.752 })))));
    expect(Math.abs(p.tx - 49.731)).toBeLessThan(1e-5);
    expect(Math.abs(p.ty + 79.752)).toBeLessThan(1e-5);
  });

  it('leaves out the keys the PLC defaults to 0', () => {
    const p: any = decode(encodePacket(cmd.TapeCycle(tapeArgs())));
    expect('tin' in p).toBe(false);
    expect('motion_progress' in p).toBe(false);
    const q: any = decode(encodePacket(cmd.TapeCycle(tapeArgs({ motion_progress: 0.5 }))));
    expect(q.motion_progress).toBeCloseTo(0.5, 6);
  });

  it('other commands keep float64', () => {
    const p: any = decode(encodePacket({ type: 'M', cmd: 'G1', X: 0.1 }));
    expect(p.X).toBe(0.1);
  });
});
