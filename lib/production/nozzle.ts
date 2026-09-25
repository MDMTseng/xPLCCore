// Nozzle sequences: pick, place, drop and pick back out of the tape.
//
// Each is a short burst of queued PLC moves and output changes. They were
// written out inline (the drop sequence three times) in CalibPage; the
// parameters are in params.ts (NOZZLE, GEOMETRY).

import { cmd } from '../protocol';
import { GEOMETRY, NOZZLE } from './params';
import { IO_PINS, bit } from './io';
import type { Machine } from './machine';

const SUCK = bit(IO_PINS.O.Nozzle_suck);
const BLOW = bit(IO_PINS.O.Nozzle_blow);

export type Pose = { X?: number; Y?: number; Z: number; A?: number };

/** Pick a part off the feeder plate at `p` (Z is the part's Z). */
export async function pickFromFeeder(m: Machine, p: { X: number; Y: number; Z: number; A: number }) {
  await m.send(cmd.G1({ X: p.X, Y: p.Y, A: p.A }));
  await m.send(cmd.G1({ Z: p.Z + GEOMETRY.PICK_Z_LIFT }));
  m.sendNoWait(cmd.M4({ pin: SUCK, state: SUCK }));
  m.sendNoWait(cmd.G4(NOZZLE.PICK_DWELL_S));
  await m.send(cmd.G1({ Z: GEOMETRY.SAFE_Z }));
}

/**
 * Put the held part down at `p` and lift. `beforeDescent` runs once the
 * approach above the slot is queued (the cycle's "[STEP] place object"
 * checkpoint, where step mode can stop the arm above the tape).
 */
export async function placePart(m: Machine, p: Pose, beforeDescent?: () => Promise<unknown>) {
  await m.send(cmd.G1({ X: p.X, Y: p.Y, Z: p.Z + 5, A: p.A }));
  if (beforeDescent) await beforeDescent();
  await m.send(cmd.G1({ Z: p.Z }));
  await m.send(cmd.G4(NOZZLE.PLACE_DWELL_S));
  await m.send(cmd.M4({ pin: SUCK, state: 0 }));
  await m.send(cmd.M4({ pin: BLOW, state: BLOW, reset_ms: NOZZLE.PLACE_BLOW_MS }));  // vacuum break
  await m.send(cmd.G4(NOZZLE.PLACE_LIFT_DWELL_S));
  await m.send(cmd.G1({ Z: GEOMETRY.SAFE_Z }));
}

/** Drop the held part where the arm is (above a bin or the feeder). */
export function dropPart(m: Machine) {
  m.sendNoWait(cmd.G4(0.01));
  m.sendNoWait(cmd.M4({ pin: SUCK, state: 0 }));
  m.sendNoWait(cmd.M4({ pin: BLOW, state: BLOW, reset_ms: NOZZLE.TOSS_BLOW_MS }));  // vacuum break
  m.sendNoWait(cmd.G4(0.01));
}

/** Move over `bin` at travel height and drop the held part there.
 *  `abort`: cut short whatever move is running (G1 abort = SoftMotion
 *  Aborting) instead of queueing behind it, e.g. the head start toward
 *  the tape when the part turns out NG. */
export async function tossTo(m: Machine, bin: { X: number; Y: number }, o: { abort?: boolean } = {}) {
  await m.send(cmd.G1({ X: bin.X, Y: bin.Y, Z: GEOMETRY.SAFE_Z, ...(o.abort ? { abort: true } : {}) }));
  dropPart(m);
}

/** Pick an NG part back out of tape slot position (x, y). */
export async function pickFromTape(m: Machine, x: number, y: number) {
  await m.send(cmd.G1({ X: x, Y: y }));
  await m.send(cmd.G1({ Z: GEOMETRY.SLOT_LOCATION.Z - NOZZLE.NG_PICK_DEPTH_MM }));
  m.sendNoWait(cmd.G4(0.01));
  m.sendNoWait(cmd.M4({ pin: SUCK, state: SUCK }));
  m.sendNoWait(cmd.G4(NOZZLE.NG_PICK_DWELL_S));
  await m.send(cmd.G1({ Z: GEOMETRY.SAFE_Z }));
}
