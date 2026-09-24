// I/O map, event-log marks and vision check ids of the cell.
//
// Single source of truth: if a pin moves, change it here, not at a call
// site. `as const` keeps the literal numbers.

import { cmd } from '../protocol';

export const IO_PINS = {
  /** Digital inputs (bit index in GET_DIGITAL_INPUT state). */
  I: {
    ReelLacking: 8,
    ReelTapeHTension: 9,
    ReelPressRollerInPlace: 10,
    PackedReelNoProtrusion: 11,
  },
  /** Digital outputs (bit index in M4 pin masks). */
  O: {
    Nozzle_suck: 0,
    Nozzle_blow: 1,
    CAM_Top_SideLight: 3,
    FlexVib_brake: 5,
    ReelAdv: 6,
    ReelWheelFeed: 7,
    CAM_Side: 8,
    CAM_Side_Light0: 9,
    CAM_Btm: 10,
    CAM_Btm_Light0: 11,
    CAM_FlexFeeder: 12,
    CAM_FlexFeeder_Light0: 13,
    CAM_Top: 14,
    CAM_Top_Light0: 15,
  },
} as const;

export const bit = (pin: number) => 1 << pin;

/** Host marks in the PLC event log (cmd.EvtMark). Keep in step with
 *  tools/sim/event_log.py MARKS. */
export const EVT = {
  TOP_RESULT: 1,
  BTM_RESULT: 2,
  SIDE_RESULT: 3,
  FEEDER_RESULT: 4,
  VIB_ON: 5,
  VIB_OFF: 6,
  FEEDER_LIGHT_ON: 7,
  FEEDER_LIGHT_OFF: 8,
  PLACE: 9,
  TOSS: 10,
} as const;

/** Vision check ids: the ids the vision plugin replies with. */
export const VISION_CHECK = {
  feeder: { id: 104500, name: 'FFeederCheckData', mark: EVT.FEEDER_RESULT },
  side: { id: 114500, name: 'SideCheckData', mark: EVT.SIDE_RESULT },
  btm: { id: 124500, name: 'BTMCheckData', mark: EVT.BTM_RESULT },
  top: { id: 134500, name: 'TOPCheckData', mark: EVT.TOP_RESULT },
} as const;

export type VisionCheck = keyof typeof VISION_CHECK;

/** Camera + light strobe: both pins high together, the PLC resets them
 *  after reset_ms (the strobe driver wants equal pulses). */
export function camTrig(camPin: number, lightPin: number, opts: {
  reset_ms: number;
  motion_progress?: number;
  motion_id_offset?: number;
}) {
  const mask = bit(camPin) | bit(lightPin);
  return cmd.M4({ pin: mask, state: mask, ...opts });
}
