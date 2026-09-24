// The tape step: advance the carrier tape and shoot the top camera.
//
// One PLC command (TAPE_CYCLE, plc.md §Direction step 3), sequenced on the
// PLC's 1 ms task instead of four host round trips:
//  1. wait for the last queued move (the Z rise after placing) to start --
//     even without an advance: the place moves are only queued when this
//     runs, so the arm may still be on its way *to* the tape;
//  2. advance `cells` and wait for the reel to stop; the PLC counts them
//     into its retained plan (cells + kind);
//  3. arm the two top-camera shots (side light, then front light) to fire
//     once the arm is TAPE.TOP_CAM_CLEAR_MM away from above the middle
//     slot, i.e. out of the camera's view.

import { cmd } from '../protocol';
import { GEOMETRY, TAPE } from './params';
import { IO_PINS, bit } from './io';
import type { Machine } from './machine';

/** What the top camera reports for the cells in view (slot 0..2). */
export type TopView = {
  is_clear: number[];
  is_OK: number[];
  post_check_advCount: number;
  locHole: { status: number; x: number; y: number; mmpp: number };
};

export type TapeStepResult = {
  /** Top-camera view after the step; undefined when vision timed out. */
  view: TopView | undefined;
  /** PLC's cell count after the step (undefined on an older PLC program). */
  cellsDone?: number;
  /** The PLC's plan disagreed with the kind/size of this advance. */
  planErr: boolean;
};

export type TapeStepOptions = {
  cells: number;
  kind: 'pack' | 'empty';
  /** When the step may start, relative to the motion queued now. */
  trigger?: { motion_id_offset: number; motion_progress: number };
};

let topShotEventId = TAPE.TOP_SHOT_EVENT_ID_BASE;

/** Pin sequence for the two top shots: [delay_ms, mask, state] triples. */
function topShotSequence(): number[] {
  const side = bit(IO_PINS.O.CAM_Top_SideLight) | bit(IO_PINS.O.CAM_Top);
  const front = bit(IO_PINS.O.CAM_Top_Light0) | bit(IO_PINS.O.CAM_Top);
  return [
    TAPE.REEL_SETTLE_MS, side, side,
    1, side, 0,
    TAPE.TOP_SHOT_GAP_MS, front, front,
    1, front, 0,
  ];
}

export async function tapeStep(m: Machine, o: TapeStepOptions): Promise<TapeStepResult> {
  const trigger = o.trigger ?? { motion_id_offset: -1, motion_progress: 0 };
  const rep = await m.send(cmd.TapeCycle({
    ...trigger,
    distance: o.cells > 0 ? o.cells * TAPE.REEL_CELL_DISTANCE : 0,
    ...TAPE.REEL_MOVE,
    // the middle slot, at travel height: a nozzle rising straight up from a
    // slot still counts as "in view"
    x: GEOMETRY.SLOT_LOCATION.X + GEOMETRY.SLOT_PITCH_MM,
    y: GEOMETRY.SLOT_LOCATION.Y,
    z: GEOMETRY.SAFE_Z,
    radius: TAPE.TOP_CAM_CLEAR_MM,
    pin_op_seq: topShotSequence(),
    event_id: ++topShotEventId,
    timeout_ms: TAPE.REEL_STOP_TIMEOUT_MS + 3000,
    cells: o.cells,
    kind: o.kind,
  }), true, TAPE.REEL_STOP_TIMEOUT_MS + 5000);

  // The shots fire only after this reply (the arm must still leave the
  // sphere, and vision needs its processing time), so registering the wait
  // now cannot miss the result -- and its budget is not spent on the reel.
  const top = await m.waitVision('top');
  return {
    view: top === undefined ? undefined : { ...top, post_check_advCount: 0 },
    cellsDone: typeof rep?.cells_done === 'number' ? rep.cells_done : undefined,
    planErr: rep?.plan_err === true,
  };
}
