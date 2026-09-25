// Flex feeder: spread the parts, brake, light, shoot, read the result.
//
// Vibration and the top light go over the Modbus bridge (FeederIO); the
// brake and the camera strobe are PLC outputs. The timings are host timers
// today -- FEEDER_CYCLE on the PLC (plc.md §Direction step 4) would take
// the jitter out of them.

import { cmd } from '../protocol';
import { FEEDER } from './params';
import { EVT, IO_PINS, bit } from './io';
import type { Machine } from './machine';

/** A part the feeder camera found. */
export type FeederPart = {
  x: number;
  y: number;
  angle_deg: number;
  /** 1 = nothing touches the part (it can be picked). */
  surround_clear: number;
  center_clear: number;
};

/** Vibration channels on the feeder bridge. */
export const VIB_SPREAD = 10;
export const VIB_STORAGE = 0x1D;

// Feeder bridge writes (Modbus) are not awaited -- the vibration and light
// timings run on host timers -- but they are no longer ignored: a failed
// write is kept here and fails the next feeder step, so a dead bridge
// (plate never shaken, light never on) stops the run instead of feeding
// an empty plate forever (review 2026-09-26 #16).
let feederFault: unknown;

/** Forget a failure from an earlier run (runCycles, at its start). */
export function clearFeederFault() {
  feederFault = undefined;
}

function raiseFeederFault() {
  if (feederFault === undefined) return;
  const e: any = feederFault;
  feederFault = undefined;
  throw new Error('送料盤控制失敗 / feeder bridge write failed: ' + (e?.message ?? e));
}

function track(r: unknown) {
  if (r && typeof (r as any).then === 'function') {
    (r as Promise<unknown>).catch((e) => { feederFault ??= e; });
  }
}

async function vibrate(m: Machine, channel: number, ms: number) {
  m.mark(EVT.VIB_ON);
  track(m.feeder.von(channel));
  await m.delay(ms);
  m.mark(EVT.VIB_OFF);
  track(m.feeder.voff(channel));
}

/** Shoot the plate and return what the camera saw (all parts). */
export async function inspectFeeder(m: Machine, o: { shake: boolean }): Promise<FeederPart[]> {
  raiseFeederFault();
  if (o.shake) {
    // start once the arm has begun leaving the plate
    await m.send(cmd.WaitForTriggerMotionProgress({ motion_progress: 0 }));
    await vibrate(m, VIB_SPREAD, FEEDER.SPREAD_VIB_MS);
    await m.delay(FEEDER.SETTLE_MS);
    await m.send(cmd.M4({
      pin: bit(IO_PINS.O.FlexVib_brake), state: bit(IO_PINS.O.FlexVib_brake),
      motion_id_offset: 0, motion_progress: 0, reset_ms: FEEDER.BRAKE_MS,
    }));
    await m.delay(FEEDER.BRAKE_MS);
    track(m.feeder.voff(VIB_STORAGE));
  } else {
    await m.send(cmd.WaitForTriggerMotionProgress({ motion_progress: 1 }));
  }

  const result = m.waitVision('feeder');
  void (async () => {
    m.mark(EVT.FEEDER_LIGHT_ON);
    track(m.feeder.top_light_on());
    await m.delay(FEEDER.LIGHT_LEAD_MS);
    await m.send(cmd.M4({
      pin: bit(IO_PINS.O.CAM_FlexFeeder), state: bit(IO_PINS.O.CAM_FlexFeeder),
      reset_ms: FEEDER.CAMERA_STROBE_MS,
    }));
    await m.delay(FEEDER.CAMERA_STROBE_MS);
    m.mark(EVT.FEEDER_LIGHT_OFF);
    track(m.feeder.top_light_off());
  })().catch((e) => { feederFault ??= e; });

  // Timed out: treat the plate as empty; the next cycle shakes and looks again.
  const raw = ((await result) ?? []) as { x: number; y: number; ang: number; inner: number; outer: number }[];
  raiseFeederFault();
  return raw.map((p) => ({
    x: p.x, y: p.y, angle_deg: p.ang, surround_clear: p.outer, center_clear: p.inner,
  }));
}

/** Refill cycle: shake, shoot, and top the plate up from storage when it
 *  runs low. Returns the parts that can be picked. */
export async function refillFeeder(m: Machine): Promise<FeederPart[]> {
  await m.send(cmd.WaitForTriggerMotionProgress({ motion_id_offset: 0, motion_progress: 0.02 }));
  const parts = await inspectFeeder(m, { shake: true });
  if (parts.length < FEEDER.REFILL_BELOW_PARTS) {
    void vibrate(m, VIB_STORAGE, FEEDER.REFILL_VIB_MS);
  }
  return parts.filter((p) => p.surround_clear === 1 && p.center_clear === 1);
}
